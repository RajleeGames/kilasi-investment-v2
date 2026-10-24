"""Consent-aware SMS drafts and explicit, opt-in Beem delivery.

Never sends during save(), migrations, imports or signal handlers. Manual POST
confirmation is mandatory, and the global feature switch defaults to OFF.
"""
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from sms_center.models import SmsContact, SmsCampaign, SmsRecipient, SmsSenderProfile
from sms_center.services import sms_segments, provider_configured, _send_one_payload
from .models import KilasiSmsDispatch

MAX_SMS_PER_DRAFT = 50
ALLOWED_FIELDS = {'name','first_name','phone','reference','pickup','destination',
                  'date','amount','status','branch','vehicle','driver','customer','customer_phone'}
VARIABLE_PATTERN = re.compile(r'\{\{\s*([a-z_]+)\s*\}\}|\{([a-z_]+)\}|\[([a-z_]+)\]', re.I)


def personalized(body, row):
    text = (body or '').strip()
    if not text or len(text) > 1000:
        raise ValidationError('Enter a message of 1 to 1000 characters.')
    name = (row.get('name') or 'Mteja').strip()
    first = name.split()[0] if name else 'Mteja'
    fields = {**row, 'name':name, 'first_name':first,
              'date': str(row.get('date') or ''), 'phone':str(row.get('phone') or '')}
    def replace(m):
        field = next(x for x in m.groups() if x is not None).lower()
        if field not in ALLOWED_FIELDS:
            raise ValidationError(f'Unknown SMS variable: {field}')
        return str(fields.get(field) or '')
    message = VARIABLE_PATTERN.sub(replace, text)
    if not re.search(r'\{\{\s*(name|first_name)\s*\}\}|\{(name|first_name)\}|\[(name|first_name)\]', text, re.I):
        if not re.match(r'^(ndugu|hello|hi|habari)\b', message, re.I):
            message = f'Ndugu {first}, {message}'
    if len(message) > 1000:
        raise ValidationError('One personalized SMS is too long (over 1,000 characters).')
    return message


def blocked_numbers(phones):
    """Respect existing Novman SMS opt-outs even for dynamically derived customers."""
    denied=set()
    for offset in range(0,len(phones),450):
        part=phones[offset:offset+450]
        denied.update(SmsContact.objects.filter(normalized_phone__in=part)
                      .filter(Q(sms_allowed=False)|Q(is_active=False))
                      .values_list('normalized_phone',flat=True))
    return denied


@transaction.atomic
def draft(*, department, branch, creator, source, title, sender_id, body, recipients):
    approved = set(SmsSenderProfile.objects.filter(is_active=True).values_list('sender_id',flat=True))
    if sender_id not in approved:
        raise ValidationError('Select an active sender ID approved in SMS Center.')
    if not title or len(title) > 135:
        raise ValidationError('Enter a title (maximum 135 characters).')
    if len(recipients) > MAX_SMS_PER_DRAFT:
        raise ValidationError(f'Choose at most {MAX_SMS_PER_DRAFT} customers per draft.')
    if not recipients:
        raise ValidationError('Select at least one customer.')
    seen = set()
    chosen = []
    blocked = blocked_numbers([r['phone'] for r in recipients])
    for row in recipients:
        if row['phone'] in blocked or row['phone'] in seen:
            continue
        seen.add(row['phone'])
        chosen.append((row, personalized(body, row)))
    if not chosen:
        raise ValidationError('All selected numbers were blocked or duplicates.')
    campaign = SmsCampaign.objects.create(
        title=f'KI {department.title()} | {title}', sender_id=sender_id,
        message=body.strip(), recipient_count=len(chosen),
        segment_count=max(sms_segments(message) for _,message in chosen),
        estimated_units=sum(sms_segments(message) for _,message in chosen),
        status=SmsCampaign.Status.DRAFT, created_by=creator,
    )
    for row,message in chosen:
        SmsRecipient.objects.create(campaign=campaign, name=row['name'],
                                    phone=row['phone'], personalized_message=message)
    return KilasiSmsDispatch.objects.create(
        department=department, branch=branch, source=source,
        campaign=campaign, created_by=creator,
    )


def live_ready():
    return bool(getattr(settings, 'KILASI_SMS_LIVE_ENABLED', False) and provider_configured())


def confirm_send(*, dispatch_id, department, branch):
    """Claims draft only once. No retries after unknown provider outcome.

    Mark 'sending' before network requests so double-clicks cannot resend.
    Any crash/timeout requires a human to reconcile provider logs.
    """
    with transaction.atomic():
        lookup = {'pk':dispatch_id, 'department':department}
        if department == 'retail':
            lookup['branch'] = branch
        record = KilasiSmsDispatch.objects.select_related('campaign').get(**lookup)
        if record.status != 'draft':
            raise ValidationError('This draft was already submitted. Review its history; do not resend.')
        if not live_ready():
            raise ValidationError('Live SMS is OFF or Beem credentials are missing. No SMS was sent.')
        if not SmsSenderProfile.objects.filter(sender_id=record.campaign.sender_id, is_active=True).exists():
            raise ValidationError('Sender ID is inactive.')
        # Compare-and-swap prevents two confirmations from both sending, even on SQLite.
        claimed=KilasiSmsDispatch.objects.filter(pk=dispatch_id, status='draft').update(
            status='sending', confirmed_at=timezone.now())
        if claimed != 1:
            raise ValidationError('Draft already claimed; never send twice.')
        campaign=record.campaign
        campaign.status=SmsCampaign.Status.SENDING
        campaign.started_at=timezone.now()
        campaign.save(update_fields=['status','started_at','updated_at'])
    accepted=0
    failed=0
    responses=[]
    for rec in campaign.recipients.order_by('pk').all():
        # Opt-out may have changed *since* draft creation.
        if blocked_numbers([rec.phone]):
            rec.status=SmsRecipient.Status.FAILED
            rec.error_message='Recipient blocked SMS.'
            failed+=1
        else:
            try:
                ok,code,body=_send_one_payload(campaign.sender_id,rec.personalized_message,[rec])
                if isinstance(body,dict) and body.get('successful') is False:
                    ok=False
                if ok:
                    rec.status=SmsRecipient.Status.SENT
                    rec.sent_at=timezone.now()
                    rec.provider_id=str(rec.pk)
                    rec.provider_status='accepted'
                    rec.error_message=''
                    accepted+=1
                else:
                    rec.status=SmsRecipient.Status.FAILED
                    rec.error_message=f'Provider rejected/unknown status ({code}). Check Beem before retry.'[:255]
                    failed+=1
                responses.append({'recipient_id':rec.pk, 'http_status':code, 'accepted':bool(ok)})
            except Exception:
                rec.status=SmsRecipient.Status.FAILED
                rec.error_message='Network/error: check Beem before any manual retry.'
                failed+=1
            # Keep DB durable after each recipient; no automated retries.
        rec.save(update_fields=['status','sent_at','provider_id','provider_status','error_message','updated_at'])
    state = 'partial' if accepted and failed else 'sent' if accepted else 'failed'
    campaign.status={'partial':SmsCampaign.Status.PARTIAL,'sent':SmsCampaign.Status.SENT,
                     'failed':SmsCampaign.Status.FAILED}[state]
    campaign.completed_at=timezone.now()
    campaign.provider_response={'responses':responses,'note':'Accepted means submitted, not confirmed delivered.'}
    campaign.save(update_fields=['status','completed_at','provider_response','updated_at'])
    record.status=state
    record.save(update_fields=['status'])
    return record, accepted, failed
