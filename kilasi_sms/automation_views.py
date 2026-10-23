from types import SimpleNamespace
"""Branch-isolated SMS automation rules and manual delivery confirmation."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from django.views.decorators.http import require_http_methods, require_POST
from sms_center.models import SmsTemplate, SmsSenderProfile
from kilasi_billing.models import DeliveryNote
from .models import KilasiSmsAutomationRule, KilasiSmsAutomaticJob, KilasiDeliveryConfirmation
from .automation_definitions import EVENTS
from .permissions import scope
from .automation import enqueue_event


@require_http_methods(['GET','POST'])
def automations(request):
    department,branch=scope(request)
    ns=request.resolver_match.namespace
    url=reverse(f'{ns}:automations')
    allowed={key:(name,cat,tmpl) for key,name,cat,tmpl in EVENTS if cat==department}
    senders=SmsSenderProfile.objects.filter(is_active=True).order_by('-is_default','sender_id')
    templates=SmsTemplate.objects.filter(is_active=True,category=f'Kilasi {department.title()}').order_by('name')
    if request.method=='POST':
        event=(request.POST.get('event_key') or '').strip()
        if event not in allowed:
            return HttpResponseForbidden('Event not available to this department.')
        active=request.POST.get('enabled')=='on'
        if active and request.POST.get('acknowledge')!='on':
            messages.error(request,'Confirm customer relationship, SMS consent and notification review before enabling.')
            return redirect(url)
        sender=(request.POST.get('sender_id') or '').strip().upper()
        template_id=(request.POST.get('template_id') or '').strip()
        template=templates.filter(pk=template_id).first() if template_id.isdigit() else None
        if not template or (sender and not senders.filter(sender_id=sender).exists()) or (active and not sender):
            messages.error(request,'Choose an active department template and a registered sender ID.')
            return redirect(url)
        with transaction.atomic():
            rule,created=KilasiSmsAutomationRule.objects.select_for_update().get_or_create(
                branch=branch,event_key=event,
                defaults={'department':department,'template':template,'sender_id':sender,'enabled':False})
            if rule.department != department:
                raise PermissionDenied('The automation rule is assigned to a different department.')
            was_enabled=rule.enabled
            rule.template=template
            rule.sender_id=sender
            rule.enabled=active
            if active and not was_enabled:rule.enabled_at=timezone.now()
            rule.changed_by=request.user
            rule.full_clean()
            rule.save()
        messages.success(request,'Automation rule saved. No messages are sent from the settings page.')
        return redirect(url)

    existing={x.event_key:x for x in KilasiSmsAutomationRule.objects.filter(branch=branch,department=department)
              .select_related('template')}
    preset_by_name={t.name:t for t in templates}
    rules=[]
    for key,name,cat,preset in EVENTS:
        if cat != department:continue
        row=existing.get(key)
        rules.append({'key':key,'name':name,'rule':row,'template_id':row.template_id if row else
                      (preset_by_name[preset].pk if preset in preset_by_name else None)})
    jobs=list(KilasiSmsAutomaticJob.objects.filter(**({'branch':branch,'rule__department':department} if department=='retail' else {'rule__department':department}))
          .select_related('campaign').order_by('-created_at')[:35])
    from .automation import can_manually_retry
    for job in jobs: job.can_retry_from_page = department == 'transport' and can_manually_retry(job)
    return render(request,'kilasi_sms/automations.html',{
        'department':department,'department_name':'HAMA NA KILASI' if department=='transport' else 'Kilasi Retail',
        'branch':(branch if department=='retail' else SimpleNamespace(name='HAMA NA KILASI')),'rules':rules,'senders':senders,'templates':templates,
        'jobs':jobs,'automations_on':bool(getattr(settings,'KILASI_SMS_AUTOMATION_ENABLED',False)),
        'save_url':url,
        'sms_url':reverse(f'{ns}:hub'),
    })


@require_POST
def retry_transport_sms(request, pk):
    from django.core.exceptions import ValidationError
    from .automation import manual_retry_job
    department, branch = scope(request)
    if department != 'transport':
        return HttpResponseForbidden('Only HAMA NA KILASI managers can retry transport SMS.')
    job = get_object_or_404(KilasiSmsAutomaticJob, pk=pk, branch__isnull=True,
                            event_key__startswith='transport.')
    try:
        manual_retry_job(job.pk, request.user, request.POST.get('reason',''),
                         confirmed_unsubmitted=(request.POST.get('beem_checked')=='on'))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    else:
        messages.success(request, 'SMS queued for worker processing. This page did not send it immediately.')
    source=(request.POST.get('return_to') or '')
    if source == 'trips':
        return redirect('transport:trips')
    return redirect('transport_sms:automations')


@require_POST
def mark_delivered(request,pk):
    department,branch=scope(request)
    if department != 'retail':return HttpResponseForbidden('Only Retail can confirm product delivery.')
    note=get_object_or_404(DeliveryNote.objects.select_related('sale'),pk=pk,sale__branch=branch,
                           sale__status='completed')
    receiver=(request.POST.get('received_by') or '').strip()
    if not receiver or len(receiver)>160:
        messages.error(request,'Enter the name of the person who received the goods.')
        return redirect('billing:delivery_detail',pk=pk)
    with transaction.atomic():
        row,created=KilasiDeliveryConfirmation.objects.get_or_create(note=note,defaults={
            'received_by':receiver,'confirmed_by':request.user,
            'note_text':(request.POST.get('notes') or '')[:250],
        })
        if created:
            transaction.on_commit(lambda:enqueue_event('retail.delivery_completed',DeliveryNote,note.pk))
    messages.success(request,'Delivery confirmed. SMS is queued only if the corresponding automation rule is enabled.'
                     if created else 'Delivery was already confirmed; no duplicate SMS will be created.')
    return redirect('billing:delivery_detail',pk=pk)
