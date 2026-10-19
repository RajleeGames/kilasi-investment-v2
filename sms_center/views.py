import csv
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.decorators import admin_required
from core.models import Customer

from .forms import SmsContactForm, SmsSenderProfileForm, SmsTemplateForm
from .models import (
    SmsCampaign,
    SmsContact,
    SmsImportBatch,
    SmsRecipient,
    SmsSenderProfile,
    SmsTemplate,
)
from .services import (
    apply_delivery_report,
    deliver_campaign,
    get_beem_balance,
    normalize_phone,
    parse_contact_csv,
    parse_manual_recipients,
    provider_configured,
    render_personalized_message,
    sms_segments,
)


def _sender_options():
    """Prefer the configured/approved environment sender over stale Jane profiles.

    This is a UI default only; Beem still needs to approve sender IDs.
    Historical sender records are never rewritten.
    """
    preferred = (getattr(settings, "BEEM_SENDER_ID", "NOVMAN") or "NOVMAN").strip().upper()
    profiles = list(SmsSenderProfile.objects.filter(is_active=True))
    values = []
    seen = set()
    for profile in profiles:
        sender_id = profile.sender_id.strip().upper()
        if sender_id and sender_id not in seen:
            values.append({
                "value": sender_id,
                "label": profile.label or sender_id,
                "default": bool(profile.is_default),
            })
            seen.add(sender_id)

    if preferred and preferred not in seen:
        values.append({
            "value": preferred,
            "label": f"{preferred} · Environment",
            "default": False,
        })
    # Display the actual configured provider sender by default. A saved active
    # sender profile can still be selected explicitly when Beem approves it.
    selected = next((v for v in values if v["value"] == preferred), None)
    if selected is None:
        selected = next((v for v in values if v["default"]), values[0] if values else None)
    for item in values:
        item["default"] = item is selected
    return values


def _blocked_numbers():
    return set(
        SmsContact.objects.filter(sms_allowed=False)
        .exclude(normalized_phone="")
        .values_list("normalized_phone", flat=True)
    )


def _page_context():
    return {
        "provider_configured": provider_configured(),
        "default_sender": getattr(settings, "BEEM_SENDER_ID", ""),
    }


@admin_required
def dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    campaigns = SmsCampaign.objects.select_related("created_by")
    recipients = SmsRecipient.objects.all()
    return render(request, "sms_center/dashboard.html", {
        **_page_context(),
        "sms_balance": get_beem_balance(),
        "contact_count": SmsContact.objects.filter(is_active=True, sms_allowed=True).count(),
        "customer_count": Customer.objects.filter(is_active=True).exclude(phone="").count(),
        "campaigns_month": campaigns.filter(created_at__date__gte=month_start).count(),
        "sent_today": recipients.filter(
            sent_at__date=today,
            status__in=[SmsRecipient.Status.SENT, SmsRecipient.Status.DELIVERED],
        ).count(),
        "delivered_today": recipients.filter(delivered_at__date=today).count(),
        "failed_today": recipients.filter(created_at__date=today, status=SmsRecipient.Status.FAILED).count(),
        "recent_campaigns": campaigns[:8],
        "sender_options": _sender_options(),
        "last_24h": now - timedelta(hours=24),
    })


@admin_required
def send_sms(request):
    customers = list(
        Customer.objects.filter(is_active=True)
        .exclude(phone="")
        .order_by("name")
        .values("id", "name", "phone")
    )
    contacts = list(
        SmsContact.objects.filter(is_active=True, sms_allowed=True)
        .order_by("name", "normalized_phone")
        .values("id", "name", "phone", "normalized_phone", "group_name")
    )
    blocked = _blocked_numbers()
    preselect_customer = request.GET.get("customer", "").strip()

    customer_rows = []
    for customer in customers:
        normalized = normalize_phone(customer["phone"])
        if not normalized:
            continue
        customer_rows.append({
            **customer,
            "normalized": normalized,
            "blocked": normalized in blocked,
            "preselected": preselect_customer.isdigit() and int(preselect_customer) == customer["id"],
        })

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        sender_id = request.POST.get("sender_id", "").strip().upper()
        message_body = request.POST.get("message", "").strip()
        selected_tokens = request.POST.getlist("recipients")
        manual_text = request.POST.get("manual_recipients", "")
        save_manual = request.POST.get("save_manual") == "on"
        manual_group = request.POST.get("manual_group", "").strip()

        valid_senders = {item["value"] for item in _sender_options()}
        if sender_id not in valid_senders:
            messages.error(request, "Choose a valid active Sender ID.")
        elif not message_body:
            messages.error(request, "Write the SMS message before sending.")
        elif len(message_body) > 1000:
            messages.error(request, "Message is too long. Keep SMS content under 1,000 characters.")
        elif not provider_configured():
            messages.error(request, "Beem SMS is not configured on this server yet.")
        else:
            resolved = {}
            customer_ids = []
            contact_ids = []
            for token in selected_tokens:
                if token.startswith("customer:") and token.split(":", 1)[1].isdigit():
                    customer_ids.append(int(token.split(":", 1)[1]))
                elif token.startswith("contact:") and token.split(":", 1)[1].isdigit():
                    contact_ids.append(int(token.split(":", 1)[1]))

            for customer in Customer.objects.filter(pk__in=customer_ids, is_active=True):
                phone = normalize_phone(customer.phone)
                if phone and phone not in blocked:
                    resolved[phone] = {
                        "name": customer.name,
                        "phone": phone,
                        "customer": customer,
                        "contact": None,
                    }

            for contact in SmsContact.objects.filter(
                pk__in=contact_ids,
                is_active=True,
                sms_allowed=True,
            ):
                phone = contact.normalized_phone or normalize_phone(contact.phone)
                if phone:
                    resolved[phone] = {
                        "name": contact.name,
                        "phone": phone,
                        "customer": contact.customer,
                        "contact": contact,
                    }

            for item in parse_manual_recipients(manual_text):
                if item["phone"] in blocked:
                    continue
                contact = SmsContact.objects.filter(normalized_phone=item["phone"]).first()
                if contact and not contact.sms_allowed:
                    continue
                if save_manual:
                    contact, _ = SmsContact.objects.update_or_create(
                        normalized_phone=item["phone"],
                        defaults={
                            "phone": item["phone"],
                            "name": item["name"] or (contact.name if contact else ""),
                            "group_name": manual_group or (contact.group_name if contact else ""),
                            "source": SmsContact.Source.MANUAL,
                            "sms_allowed": True,
                            "is_active": True,
                        },
                    )
                resolved[item["phone"]] = {
                    "name": item["name"] or (contact.name if contact else ""),
                    "phone": item["phone"],
                    "customer": contact.customer if contact else None,
                    "contact": contact,
                }

            recipients = list(resolved.values())
            max_recipients = max(1, int(getattr(settings, "SMS_MAX_RECIPIENTS_PER_SEND", 500)))
            if not recipients:
                messages.error(request, "Select at least one recipient or enter a manual phone number.")
            elif len(recipients) > max_recipients:
                messages.error(request, f"Maximum per campaign is {max_recipients:,} recipients.")
            else:
                with transaction.atomic():
                    campaign = SmsCampaign.objects.create(
                        title=title or f"SMS · {timezone.localtime():%d %b %Y %H:%M}",
                        sender_id=sender_id,
                        message=message_body,
                        recipient_count=len(recipients),
                        segment_count=sms_segments(message_body),
                        status=SmsCampaign.Status.DRAFT,
                        created_by=request.user,
                    )
                    recipient_objects = []
                    estimated_units = 0
                    for item in recipients:
                        personalized = render_personalized_message(
                            message_body,
                            name=item["name"],
                            phone=item["phone"],
                        )
                        estimated_units += sms_segments(personalized)
                        recipient_objects.append(SmsRecipient(
                            campaign=campaign,
                            customer=item["customer"],
                            contact=item["contact"],
                            name=item["name"],
                            phone=item["phone"],
                            personalized_message=personalized,
                        ))
                    SmsRecipient.objects.bulk_create(recipient_objects)
                    campaign.estimated_units = estimated_units
                    campaign.save(update_fields=["estimated_units", "updated_at"])

                deliver_campaign(campaign)
                campaign.refresh_from_db()
                if campaign.status == SmsCampaign.Status.FAILED:
                    messages.error(request, "SMS campaign could not be sent. Open it to see the provider response.")
                elif campaign.status == SmsCampaign.Status.PARTIAL:
                    messages.warning(request, "Campaign was partially sent. Review failed recipients.")
                else:
                    messages.success(request, f"SMS campaign sent to {campaign.recipient_count:,} recipient(s).")
                return redirect("sms_center:campaign_detail", pk=campaign.pk)

    initial_template = None
    template_id = request.GET.get("template", "").strip()
    if template_id.isdigit():
        initial_template = SmsTemplate.objects.filter(pk=int(template_id), is_active=True).first()

    return render(request, "sms_center/send.html", {
        **_page_context(),
        "sms_balance": get_beem_balance(),
        "customers": customer_rows,
        "contacts": contacts,
        "templates": SmsTemplate.objects.filter(is_active=True),
        "sender_options": _sender_options(),
        "max_recipients": getattr(settings, "SMS_MAX_RECIPIENTS_PER_SEND", 500),
        "initial_template": initial_template,
    })


@admin_required
def balance_json(request):
    balance = get_beem_balance(force=True)
    return JsonResponse({
        "ok": balance.get("ok", False),
        "configured": balance.get("configured", False),
        "display": balance.get("display", "Unavailable"),
        "message": balance.get("message", ""),
        "checked_at": balance.get("checked_at").isoformat() if balance.get("checked_at") else None,
    })


@admin_required
def contacts(request):
    query = request.GET.get("q", "").strip()
    group = request.GET.get("group", "").strip()
    source = request.GET.get("source", "").strip()
    queryset = SmsContact.objects.select_related("customer").all()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query) | Q(phone__icontains=query)
            | Q(normalized_phone__icontains=query) | Q(group_name__icontains=query)
        )
    if group:
        queryset = queryset.filter(group_name=group)
    if source:
        queryset = queryset.filter(source=source)
    groups = (
        SmsContact.objects.exclude(group_name="")
        .values_list("group_name", flat=True).distinct().order_by("group_name")
    )
    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return render(request, "sms_center/contacts.html", {
        **_page_context(), "page": page, "query": query, "group": group, "source": source,
        "groups": groups, "source_choices": SmsContact.Source.choices,
        "active_count": SmsContact.objects.filter(is_active=True, sms_allowed=True).count(),
        "blocked_count": SmsContact.objects.filter(sms_allowed=False).count(),
    })


@admin_required
def contact_create(request):
    form = SmsContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.source = SmsContact.Source.MANUAL
        contact.save()
        messages.success(request, "SMS contact added.")
        return redirect("sms_center:contacts")
    return render_form(request, form, "New SMS contact", "Add a reusable phone number.", "Save contact")


@admin_required
def contact_edit(request, pk):
    contact = get_object_or_404(SmsContact, pk=pk)
    form = SmsContactForm(request.POST or None, instance=contact)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "SMS contact updated.")
        return redirect("sms_center:contacts")
    return render_form(request, form, "Edit SMS contact", "Update contact details and SMS permission.", "Save changes")


@admin_required
def contact_delete(request, pk):
    contact = get_object_or_404(SmsContact, pk=pk)
    if request.method == "POST":
        name = contact.name or contact.normalized_phone
        contact.delete()
        messages.success(request, f"{name} removed from SMS contacts.")
        return redirect("sms_center:contacts")
    return render(request, "sms_center/confirm_delete.html", {
        "title": "Delete SMS contact", "object_name": contact.name or contact.normalized_phone,
        "cancel_url": "sms_center:contacts",
    })


@admin_required
@require_POST
def sync_customers(request):
    created = updated = skipped = 0
    for customer in Customer.objects.filter(is_active=True).exclude(phone=""):
        phone = normalize_phone(customer.phone)
        if not phone:
            skipped += 1
            continue
        contact = SmsContact.objects.filter(normalized_phone=phone).first()
        if contact:
            changed = False
            if not contact.customer_id:
                contact.customer = customer
                changed = True
            if not contact.name and customer.name:
                contact.name = customer.name
                changed = True
            if changed:
                contact.save()
                updated += 1
            continue
        SmsContact.objects.create(
            customer=customer,
            name=customer.name,
            phone=customer.phone,
            normalized_phone=phone,
            source=SmsContact.Source.CUSTOMER,
        )
        created += 1
    messages.success(request, f"Customer sync complete: {created} added, {updated} updated, {skipped} skipped.")
    return redirect("sms_center:contacts")


@admin_required
def import_contacts(request):
    recent_imports = SmsImportBatch.objects.select_related("created_by")[:10]
    if request.method == "POST":
        uploaded = request.FILES.get("csv_file")
        default_group = request.POST.get("group_name", "").strip()
        update_existing = request.POST.get("update_existing") == "on"
        if not uploaded:
            messages.error(request, "Choose a CSV file first.")
        else:
            try:
                rows = parse_contact_csv(uploaded)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                batch = SmsImportBatch.objects.create(
                    filename=uploaded.name[:200], group_name=default_group,
                    total_rows=len(rows), created_by=request.user,
                )
                created = updated = skipped = errors = 0
                for row in rows:
                    if not row["phone"]:
                        errors += 1
                        continue
                    contact = SmsContact.objects.filter(normalized_phone=row["phone"]).first()
                    group_name = default_group or row["group_name"]
                    if contact:
                        if not update_existing:
                            skipped += 1
                            continue
                        contact.name = row["name"] or contact.name
                        contact.phone = row["raw_phone"] or contact.phone
                        contact.group_name = group_name or contact.group_name
                        contact.source = SmsContact.Source.IMPORT
                        contact.is_active = True
                        contact.save()
                        updated += 1
                    else:
                        SmsContact.objects.create(
                            name=row["name"], phone=row["raw_phone"] or row["phone"],
                            normalized_phone=row["phone"], group_name=group_name,
                            source=SmsContact.Source.IMPORT,
                        )
                        created += 1
                batch.imported_count = created
                batch.updated_count = updated
                batch.skipped_count = skipped
                batch.error_count = errors
                batch.save()
                messages.success(request, f"CSV imported: {created} added, {updated} updated, {skipped} skipped, {errors} invalid.")
                return redirect("sms_center:contacts")
    return render(request, "sms_center/import.html", {
        **_page_context(), "recent_imports": recent_imports,
    })


@admin_required
def sample_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="novman_sms_contacts_sample.csv"'
    writer = csv.writer(response)
    writer.writerow(["name", "phone", "group"])
    writer.writerow(["Example Customer", "0712345678", "VIP Customers"])
    writer.writerow(["Another Customer", "255754000000", "Retail Customers"])
    return response


@admin_required
def templates(request):
    return render(request, "sms_center/templates.html", {
        **_page_context(), "templates": SmsTemplate.objects.select_related("created_by"),
    })


@admin_required
def template_create(request):
    form = SmsTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.created_by = request.user
        template.save()
        messages.success(request, "SMS template created.")
        return redirect("sms_center:templates")
    return render_form(request, form, "New SMS template", "Use {name}, {first_name} and {phone} for personalization.", "Save template")


@admin_required
def template_edit(request, pk):
    template = get_object_or_404(SmsTemplate, pk=pk)
    form = SmsTemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "SMS template updated.")
        return redirect("sms_center:templates")
    return render_form(request, form, "Edit SMS template", "Update the reusable message content.", "Save changes")


@admin_required
def template_delete(request, pk):
    template = get_object_or_404(SmsTemplate, pk=pk)
    if request.method == "POST":
        template.delete()
        messages.success(request, "SMS template deleted.")
        return redirect("sms_center:templates")
    return render(request, "sms_center/confirm_delete.html", {
        "title": "Delete SMS template", "object_name": template.name,
        "cancel_url": "sms_center:templates",
    })


@admin_required
def senders(request):
    return render(request, "sms_center/senders.html", {
        **_page_context(), "senders": SmsSenderProfile.objects.all(),
        "environment_sender": getattr(settings, "BEEM_SENDER_ID", ""),
    })


@admin_required
def sender_create(request):
    form = SmsSenderProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sender = form.save()
        if sender.is_default:
            SmsSenderProfile.objects.exclude(pk=sender.pk).update(is_default=False)
        messages.success(request, "Sender profile added. Use only Sender IDs approved by Beem.")
        return redirect("sms_center:senders")
    return render_form(request, form, "New Sender ID", "Add an already-approved Beem Sender ID.", "Save Sender ID")


@admin_required
def sender_edit(request, pk):
    sender = get_object_or_404(SmsSenderProfile, pk=pk)
    form = SmsSenderProfileForm(request.POST or None, instance=sender)
    if request.method == "POST" and form.is_valid():
        sender = form.save()
        if sender.is_default:
            SmsSenderProfile.objects.exclude(pk=sender.pk).update(is_default=False)
        messages.success(request, "Sender profile updated.")
        return redirect("sms_center:senders")
    return render_form(request, form, "Edit Sender ID", "Update this approved sender profile.", "Save changes")


@admin_required
def sender_delete(request, pk):
    sender = get_object_or_404(SmsSenderProfile, pk=pk)
    if request.method == "POST":
        sender.delete()
        messages.success(request, "Sender profile removed.")
        return redirect("sms_center:senders")
    return render(request, "sms_center/confirm_delete.html", {
        "title": "Delete Sender ID", "object_name": sender.sender_id,
        "cancel_url": "sms_center:senders",
    })


@admin_required
def history(request):
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    queryset = SmsCampaign.objects.select_related("created_by")
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query) | Q(message__icontains=query) | Q(sender_id__icontains=query)
        )
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "sms_center/history.html", {
        **_page_context(), "page": page, "status": status, "query": query,
        "date_from": date_from, "date_to": date_to,
        "status_choices": SmsCampaign.Status.choices,
    })


@admin_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(SmsCampaign.objects.select_related("created_by"), pk=pk)
    recipient_status = request.GET.get("status", "").strip()
    recipients = campaign.recipients.select_related("customer", "contact")
    if recipient_status:
        recipients = recipients.filter(status=recipient_status)
    page = Paginator(recipients, 50).get_page(request.GET.get("page"))
    return render(request, "sms_center/campaign_detail.html", {
        **_page_context(), "campaign": campaign, "page": page,
        "recipient_status": recipient_status,
        "recipient_status_choices": SmsRecipient.Status.choices,
    })


def render_form(request, form, title, subtitle, submit_label):
    return render(request, "sms_center/form.html", {
        **_page_context(), "form": form, "form_title": title,
        "form_subtitle": subtitle, "submit_label": submit_label,
    })


@csrf_exempt
@require_POST
def delivery_report_webhook(request):
    token = getattr(settings, "BEEM_DLR_TOKEN", "")
    if not token:
        return JsonResponse({"ok": False, "error": "DLR token is not configured"}, status=503)
    supplied = request.GET.get("token") or request.headers.get("X-SMS-DLR-Token", "")
    if supplied != token:
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)
    updated = apply_delivery_report(payload)
    return JsonResponse({"ok": True, "updated": updated})
