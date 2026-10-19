"""SMS automatic outbox. Signal handlers only enqueue; sending is a separate scheduled command.

Important: never issue a provider request in a Django save/signal, request handler,
seed, migration or a failed/rolled-back transaction.
"""
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from sms_center.models import SmsCampaign, SmsRecipient, SmsSenderProfile
from sms_center.services import normalize_phone, provider_configured, _send_one_payload, sms_segments
from .models import KilasiSmsAutomationRule, KilasiSmsAutomaticJob
from .services import personalized, blocked_numbers


def _event_row(event_key, instance):
    from core.models import Sale
    from kilasi_transport.models import Booking, Trip
    from kilasi_billing.models import DeliveryNote
    if isinstance(instance, Sale) and event_key == 'retail.sale_thanks':
        if instance.status != 'completed' or not instance.branch_id or not instance.customer_id:
            return None
        c = instance.customer
        return dict(branch=instance.branch, name=c.name, phone=c.phone,
                    reference=instance.receipt_no, amount=instance.grand_total,
                    date=timezone.localdate(instance.sold_at), status=instance.status,
                    user=instance.cashier)
    if isinstance(instance, DeliveryNote) and event_key.startswith('retail.delivery_'):
        if instance.sale.status != 'completed' or not instance.sale.branch_id:
            return None
        c = instance.sale.customer
        return dict(branch=instance.sale.branch, name=instance.delivered_to or (c.name if c else 'Mteja'),
                    phone=c.phone if c else '', reference=instance.number,
                    date=instance.issue_date, status='delivery', user=instance.created_by)
    if isinstance(instance, Booking) and event_key.startswith('transport.booking_'):
        if event_key.endswith('received') and instance.status != 'pending': return None
        if event_key.endswith('confirmed') and instance.status != 'confirmed': return None
        return dict(branch=None, name=instance.customer_name,
                    phone=instance.customer_phone, reference=instance.reference,
                    pickup=instance.origin, destination=instance.destination,
                    date=instance.requested_date, amount=instance.quoted_amount,
                    status=instance.status, user=instance.created_by)
    if isinstance(instance, Trip) and event_key.startswith('transport.trip_'):
        required={'transport.trip_reminder': 'planned', 'transport.trip_underway':'in_progress',
                  'transport.trip_completed': 'completed'}
        if instance.status != required.get(event_key):return None
        return dict(branch=None, name=instance.customer_name,
                    phone=instance.customer_phone, reference=instance.reference,
                    pickup=instance.origin, destination=instance.destination,
                    date=timezone.localtime(instance.scheduled_start).strftime('%d %b %Y %H:%M'),
                    amount=instance.agreed_fee, status=instance.status, user=instance.created_by)
    return None


def enqueue_event(event_key, model, pk):
    """Read the final committed business record; deduplicate by branch/event/source PK."""
    try:
        instance = model.objects.get(pk=pk)
        row = _event_row(event_key, instance)
        if not row:
            return None
        branch=row['branch']
        rule=(KilasiSmsAutomationRule.objects.select_related('template')
              .filter(branch=(branch if not event_key.startswith('transport.') else None), event_key=event_key, enabled=True).first())
        if not rule or not rule.enabled_at or not rule.template.is_active:
            return None
        phone=normalize_phone(row.get('phone'))
        if not phone or not phone.startswith('255') or len(phone)!=12:
            return None
        if not rule.sender_id or not SmsSenderProfile.objects.filter(sender_id=rule.sender_id, is_active=True).exists():
            return None
        if blocked_numbers([phone]):
            return None
        payload={**row, 'phone':phone, 'branch':branch.name if branch else 'HAMA NA KILASI'}
        message=personalized(rule.template.body,payload)
        due=timezone.now()
        if event_key == 'transport.trip_reminder':
            # Do not remind for trips departing within 1h or cancelled trips.
            if instance.scheduled_start <= due + timedelta(hours=1):return None
            due=max(due,instance.scheduled_start-timedelta(hours=24))
        # Explicitly enabled rules never send messages for old completed events.
        job,_=KilasiSmsAutomaticJob.objects.get_or_create(
            branch=branch,event_key=event_key,source_id=instance.pk,
            defaults=dict(rule=rule,name=payload['name'] or 'Mteja',phone=phone,body=message,
                          due_at=due,created_by=row['user']))
        return job
    except model.DoesNotExist:
        return None


def scan_trip_reminders(now=None, limit=500):
    """Run periodically. Only future planned trips; never scans prior historical trips."""
    from kilasi_transport.models import Trip
    now=now or timezone.now()
    n=0
    rules=KilasiSmsAutomationRule.objects.filter(event_key='transport.trip_reminder',branch__isnull=True,enabled=True)
    for rule in rules:
        trips=(Trip.objects.filter(status='planned',
                scheduled_start__gt=now+timedelta(hours=1),
                scheduled_start__lte=now+timedelta(hours=48))
               .order_by('scheduled_start')[:limit])
        for trip in trips:
            if enqueue_event('transport.trip_reminder',Trip,trip.pk):n+=1
    return n


def can_send_automatically():
    return bool(getattr(settings,'KILASI_SMS_LIVE_ENABLED',False)
                and getattr(settings,'KILASI_SMS_AUTOMATION_ENABLED',False)
                and provider_configured())


def _event_still_eligible(job):
    """Validate the *current* operational status immediately before sending."""
    from core.models import Sale
    from kilasi_transport.models import Booking, Trip
    from kilasi_billing.models import DeliveryNote
    k=job.event_key
    if k=='retail.sale_thanks':
        return Sale.objects.filter(pk=job.source_id,branch=job.branch,status='completed').exists()
    if k=='retail.delivery_prepared':
        return DeliveryNote.objects.filter(pk=job.source_id,sale__branch=job.branch,sale__status='completed',
                kilasi_sms_delivery_confirmation__isnull=True).exists()
    if k=='retail.delivery_completed':
        return DeliveryNote.objects.filter(pk=job.source_id,sale__branch=job.branch,
               sale__status='completed',kilasi_sms_delivery_confirmation__isnull=False).exists()
    if k=='transport.booking_received':
        return Booking.objects.filter(pk=job.source_id,status='pending').exists()
    if k=='transport.booking_confirmed':
        return Booking.objects.filter(pk=job.source_id,status='confirmed').exists()
    if k=='transport.trip_reminder':
        return Trip.objects.filter(pk=job.source_id,status='planned',
                                    scheduled_start__gt=timezone.now()+timedelta(hours=1)).exists()
    if k=='transport.trip_underway':
        return Trip.objects.filter(pk=job.source_id,status='in_progress').exists()
    if k=='transport.trip_completed':
        return Trip.objects.filter(pk=job.source_id,status='completed').exists()
    return False


def deliver_job(job_id):
    """Claim once in DB, then send exactly one Beem request; never auto-retry uncertain outcomes."""
    if not can_send_automatically():return False
    with transaction.atomic():
        job=(KilasiSmsAutomaticJob.objects.select_related('rule','branch','created_by')
             .select_for_update().get(pk=job_id))
        if job.status!='queued' or job.due_at>timezone.now():return False
        if job.event_key.startswith('transport.') and job.branch_id is not None:
            job.status='cancelled'
            job.error_text='Legacy Retail-branch Transport SMS job paused: review standalone department rules.'
            job.save(update_fields=['status','error_text'])
            return False
        if not job.rule.enabled or job.rule.event_key !=job.event_key:
            job.status='cancelled';job.error_text='Automation rule disabled or changed.'
            job.save(update_fields=['status','error_text']);return False
        if not _event_still_eligible(job):
            job.status='cancelled';job.error_text='Related event no longer eligible.'
            job.save(update_fields=['status','error_text']);return False
        if blocked_numbers([job.phone]):
            job.status='blocked';job.error_text='SMS opt-out or inactive contact.'
            job.save(update_fields=['status','error_text']);return False
        if not SmsSenderProfile.objects.filter(sender_id=job.rule.sender_id,is_active=True).exists():
            job.status='cancelled';job.error_text='Approved SMS sender unavailable.'
            job.save(update_fields=['status','error_text']);return False
        # mark BEFORE calling provider: crash or timeout => no automatic retry.
        claimed=KilasiSmsAutomaticJob.objects.filter(pk=job_id,status='queued').update(
            status='sending',submitted_at=timezone.now())
        if claimed!=1:return False
        message=SmsCampaign.objects.create(
            title=f'KI AUTO {job.event_key} #{job.source_id}',sender_id=job.rule.sender_id,
            message=job.body,recipient_count=1,segment_count=sms_segments(job.body),
            estimated_units=sms_segments(job.body),status=SmsCampaign.Status.SENDING,
            created_by=job.created_by,started_at=timezone.now())
        recipient=SmsRecipient.objects.create(campaign=message,name=job.name,phone=job.phone,
                                               personalized_message=job.body)
        job.campaign=message
        job.save(update_fields=['campaign'])
    accepted=False
    code=0
    response={}
    try:
        accepted,code,response=_send_one_payload(message.sender_id,job.body,[recipient])
        if isinstance(response,dict) and response.get('successful') is False:accepted=False
    except Exception:
        # Avoid persisting exception text containing URL secrets or personal data.
        response={'error':'Provider/network exception; verify Beem before any manual retry.'}
        accepted=False
    now=timezone.now()
    state='accepted' if accepted else 'unknown'
    recipient.status=SmsRecipient.Status.SENT if accepted else SmsRecipient.Status.FAILED
    recipient.sent_at=now if accepted else None
    recipient.provider_id=str(recipient.pk) if accepted else ''
    recipient.provider_status='accepted' if accepted else 'unknown'
    recipient.error_message='' if accepted else 'Unknown/provider failure; no automatic retry.'
    recipient.save(update_fields=['status','sent_at','provider_id','provider_status','error_message','updated_at'])
    message.status=SmsCampaign.Status.SENT if accepted else SmsCampaign.Status.FAILED
    message.completed_at=now
    message.provider_response={'http_status':code,'note':'Acceptance is not confirmed delivery.'}
    message.save(update_fields=['status','completed_at','provider_response','updated_at'])
    job.status=state
    job.error_text='' if accepted else 'Unknown outcome: inspect provider before any manual intervention.'
    job.save(update_fields=['status','error_text'])
    return accepted
