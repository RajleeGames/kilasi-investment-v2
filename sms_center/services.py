import base64
import csv
import io
import json
import math
import re
import urllib.error
import urllib.request
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import SmsCampaign, SmsRecipient


PHONE_DIGITS = re.compile(r"\D+")


def normalize_phone(value):
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""

    digits = PHONE_DIGITS.sub("", raw)
    if digits.startswith("00"):
        digits = digits[2:]

    # Tanzania: 0712345678 -> 255712345678; 712345678 -> 255712345678
    if digits.startswith("0") and len(digits) == 10:
        digits = "255" + digits[1:]
    elif len(digits) == 9 and digits[:1] in {"6", "7"}:
        digits = "255" + digits

    if not 10 <= len(digits) <= 15:
        return ""
    return digits


def sms_segments(message):
    text = message or ""
    if not text:
        return 0
    unicode_message = any(ord(char) > 127 for char in text)
    single = 70 if unicode_message else 160
    multipart = 67 if unicode_message else 153
    return 1 if len(text) <= single else math.ceil(len(text) / multipart)


def render_personalized_message(message, *, name="", phone=""):
    display_name = (name or "Customer").strip()
    first_name = display_name.split()[0] if display_name else "Customer"
    return (
        (message or "")
        .replace("{name}", display_name)
        .replace("{first_name}", first_name)
        .replace("{phone}", phone or "")
    )


def provider_configured():
    return bool(
        getattr(settings, "BEEM_API_KEY", "")
        and getattr(settings, "BEEM_SECRET_KEY", "")
        and getattr(settings, "BEEM_SENDER_ID", "")
    )


def _auth_header():
    token = base64.b64encode(
        f"{settings.BEEM_API_KEY}:{settings.BEEM_SECRET_KEY}".encode("utf-8")
    ).decode("ascii")
    return f"Basic {token}"


def _json_request(url, *, method="GET", payload=None):
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": _auth_header(),
        "User-Agent": "Novman-Auto-Spares-SMS/2.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    timeout = max(3, int(getattr(settings, "BEEM_TIMEOUT", 20)))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return True, response.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return False, exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, 0, {"error": str(exc)}


def _find_balance(value):
    preferred = {
        "credit_balance", "sms_balance", "balance", "credits",
        "available_balance", "available_credits",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in preferred:
                try:
                    return Decimal(str(item))
                except (InvalidOperation, TypeError, ValueError):
                    pass
        for item in value.values():
            found = _find_balance(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_balance(item)
            if found is not None:
                return found
    return None


def get_beem_balance(force=False):
    cache_key = "novman_sms_beem_balance"
    if not force:
        cached = cache.get(cache_key)
        if cached:
            return cached

    if not provider_configured():
        result = {
            "ok": False,
            "configured": False,
            "value": None,
            "display": "Not configured",
            "message": "Add Beem API credentials to the server environment.",
            "checked_at": timezone.now(),
        }
        cache.set(cache_key, result, 30)
        return result

    ok, status_code, body = _json_request(settings.BEEM_BALANCE_URL)
    value = _find_balance(body) if ok else None
    if value is not None:
        display = f"{value:,.2f}".rstrip("0").rstrip(".")
        message = "Live balance from Beem"
    elif ok:
        display = "Connected"
        message = "Balance response received; numeric balance was not found."
    else:
        display = "Unavailable"
        message = "Could not read the Beem balance right now."

    result = {
        "ok": ok,
        "configured": True,
        "value": value,
        "display": display,
        "message": message,
        "status_code": status_code,
        "raw": body,
        "checked_at": timezone.now(),
    }
    cache.set(cache_key, result, 60)
    return result


def _send_one_payload(sender_id, message, recipients):
    payload = {
        "source_addr": sender_id,
        "encoding": 8 if any(ord(char) > 127 for char in message) else 0,
        "schedule_time": "",
        "message": message,
        "recipients": [
            {"recipient_id": recipient.pk, "dest_addr": recipient.phone}
            for recipient in recipients
        ],
    }
    return _json_request(settings.BEEM_SEND_URL, method="POST", payload=payload)


def _provider_error_text(body, status_code):
    if isinstance(body, dict):
        for key in ("message", "error", "detail", "description"):
            if body.get(key):
                return f"{body[key]} (HTTP {status_code})"
    return f"SMS provider rejected the request (HTTP {status_code or 'connection error'})."


def deliver_campaign(campaign):
    if not provider_configured():
        campaign.status = SmsCampaign.Status.FAILED
        campaign.provider_response = {"error": "Beem SMS provider is not configured."}
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["status", "provider_response", "completed_at", "updated_at"])
        campaign.recipients.update(
            status=SmsRecipient.Status.FAILED,
            error_message="Beem SMS provider is not configured.",
        )
        return campaign

    campaign.status = SmsCampaign.Status.SENDING
    campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    recipients = list(campaign.recipients.filter(status=SmsRecipient.Status.QUEUED))
    grouped = defaultdict(list)
    total_units = 0
    for recipient in recipients:
        personalized = render_personalized_message(
            campaign.message,
            name=recipient.name,
            phone=recipient.phone,
        )
        recipient.personalized_message = personalized
        total_units += sms_segments(personalized)
        grouped[personalized].append(recipient)

    if recipients:
        SmsRecipient.objects.bulk_update(recipients, ["personalized_message", "updated_at"])

    batch_size = max(1, int(getattr(settings, "BEEM_SMS_BATCH_SIZE", 50)))
    responses = []
    for message, group in grouped.items():
        for start in range(0, len(group), batch_size):
            batch = group[start:start + batch_size]
            ok, status_code, body = _send_one_payload(campaign.sender_id, message, batch)
            responses.append({
                "ok": ok,
                "status_code": status_code,
                "recipient_ids": [recipient.pk for recipient in batch],
                "response": body,
            })
            now = timezone.now()
            if ok:
                for recipient in batch:
                    recipient.status = SmsRecipient.Status.SENT
                    recipient.sent_at = now
                    recipient.provider_id = str(recipient.pk)
                    recipient.provider_status = "accepted"
                    recipient.error_message = ""
            else:
                error_text = _provider_error_text(body, status_code)
                for recipient in batch:
                    recipient.status = SmsRecipient.Status.FAILED
                    recipient.provider_status = "failed"
                    recipient.error_message = error_text[:255]

            SmsRecipient.objects.bulk_update(
                batch,
                ["status", "sent_at", "provider_id", "provider_status", "error_message", "updated_at"],
            )

    sent = campaign.recipients.filter(
        status__in=[SmsRecipient.Status.SENT, SmsRecipient.Status.DELIVERED]
    ).count()
    failed = campaign.recipients.filter(status=SmsRecipient.Status.FAILED).count()
    if sent and failed:
        final_status = SmsCampaign.Status.PARTIAL
    elif sent:
        final_status = SmsCampaign.Status.SENT
    else:
        final_status = SmsCampaign.Status.FAILED

    campaign.status = final_status
    campaign.estimated_units = total_units
    campaign.provider_response = {"batches": responses}
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=[
        "status", "estimated_units", "provider_response", "completed_at", "updated_at",
    ])
    cache.delete("novman_sms_beem_balance")
    return campaign


def parse_manual_recipients(text):
    results = []
    seen = set()
    for raw_line in re.split(r"[\r\n;]+", text or ""):
        line = raw_line.strip()
        if not line:
            continue
        pieces = [piece.strip() for piece in line.split(",") if piece.strip()]
        if len(pieces) > 2 and all(normalize_phone(piece) for piece in pieces):
            candidates = [("", piece) for piece in pieces]
        elif len(pieces) >= 2:
            candidates = [(pieces[0], pieces[1])]
        else:
            candidates = [("", pieces[0] if pieces else line)]
        for name, raw_phone in candidates:
            phone = normalize_phone(raw_phone)
            if phone and phone not in seen:
                results.append({"name": name, "phone": phone})
                seen.add(phone)
    return results


def parse_contact_csv(uploaded_file, *, max_rows=10000):
    if uploaded_file.size > 5 * 1024 * 1024:
        raise ValueError("CSV file is too large. Maximum size is 5 MB.")
    raw = uploaded_file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must have a header row.")
    normalized_headers = {str(header).strip().lower(): header for header in reader.fieldnames}
    phone_header = next((
        normalized_headers[name]
        for name in ("phone", "phone_number", "mobile", "number", "telephone", "msisdn")
        if name in normalized_headers
    ), None)
    name_header = next((
        normalized_headers[name]
        for name in ("name", "full_name", "customer", "contact_name")
        if name in normalized_headers
    ), None)
    group_header = next((
        normalized_headers[name]
        for name in ("group", "group_name", "category", "tag")
        if name in normalized_headers
    ), None)
    if not phone_header:
        raise ValueError("CSV needs a phone column (phone, mobile, number or phone_number).")

    rows = []
    for index, row in enumerate(reader, start=2):
        if len(rows) >= max_rows:
            raise ValueError(f"CSV exceeds the maximum of {max_rows:,} rows.")
        raw_phone = row.get(phone_header, "")
        rows.append({
            "row": index,
            "name": (row.get(name_header, "") if name_header else "").strip(),
            "phone": normalize_phone(raw_phone),
            "raw_phone": str(raw_phone).strip(),
            "group_name": (row.get(group_header, "") if group_header else "").strip(),
        })
    return rows


def apply_delivery_report(payload):
    items = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("data", "recipients", "reports", "delivery_reports", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
            if isinstance(candidate, dict):
                items = [candidate]
                break
        if not items:
            items = [payload]

    updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        recipient_id = (
            item.get("recipient_id") or item.get("recipientId")
            or item.get("client_id") or item.get("id")
        )
        phone = normalize_phone(
            item.get("dest_addr") or item.get("phone") or item.get("msisdn") or ""
        )
        provider_status = str(
            item.get("status") or item.get("delivery_status") or item.get("state") or ""
        ).strip()

        queryset = SmsRecipient.objects.all()
        if recipient_id and str(recipient_id).isdigit():
            queryset = queryset.filter(pk=int(recipient_id))
        elif phone:
            queryset = queryset.filter(phone=phone).order_by("-created_at")
        else:
            continue
        recipient = queryset.first()
        if not recipient:
            continue

        normalized_status = provider_status.lower()
        if any(word in normalized_status for word in ("fail", "reject", "undeliver", "expire")):
            recipient.status = SmsRecipient.Status.FAILED
        elif any(word in normalized_status for word in ("deliver", "success")):
            recipient.status = SmsRecipient.Status.DELIVERED
            recipient.delivered_at = timezone.now()
        elif normalized_status:
            recipient.status = SmsRecipient.Status.SENT

        recipient.provider_status = provider_status[:120]
        recipient.save(update_fields=["status", "provider_status", "delivered_at", "updated_at"])
        updated += 1
    return updated
