"""Opt-in, sequential SMS batches. At-most-one provider attempt per phone/series.

This module does not alter HAMA automated journey SMS rules.
"""
from collections import Counter
from datetime import timedelta
import re
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import SmsBatchSeries, SmsBatchEntry, SmsCampaign, SmsRecipient, SmsContact, SmsSenderProfile
from .services import normalize_phone, render_personalized_message, sms_segments, provider_configured, _send_one_payload

BATCH_SIZE = 500
WORKER_LIMIT = 20


def _eligible(qs):
    # Snapshot only active explicitly allowed saved contacts; never import/enable anyone.
    for contact in qs.order_by('pk').iterator(chunk_size=1000):
        phone = normalize_phone(contact.normalized_phone)
        if phone and phone == contact.normalized_phone:
            yield contact, phone


def make_series(*, actor, title, group):
    title = (title or '').strip()[:160]
    if not title:
        raise ValidationError('Enter a campaign series title.')
    group = (group or '').strip()
    if len(group) > 100:
        raise ValidationError('Invalid contact group.')
    qs = SmsContact.objects.filter(is_active=True, sms_allowed=True)
    if group:
        if not qs.filter(group_name=group).exists():
            raise ValidationError('The selected group has no eligible contacts.')
        qs = qs.filter(group_name=group)
    with transaction.atomic():
        series = SmsBatchSeries.objects.create(title=title, group_name=group, created_by=actor)
        saved = 0
        buffer = []
        for contact, phone in _eligible(qs):
            buffer.append(SmsBatchEntry(series=series, contact=contact, phone=phone,
                                        name=(contact.name or 'Mteja').strip()[:160]))
            if len(buffer) >= 1000:
                SmsBatchEntry.objects.bulk_create(buffer, batch_size=500)
                saved += len(buffer)
                buffer = []
        if buffer:
            SmsBatchEntry.objects.bulk_create(buffer, batch_size=500)
            saved += len(buffer)
        if saved == 0:
            raise ValidationError('No eligible contact numbers. Check group and SMS permissions.')
        return series


def prepare_next(*, series_id, sender_id, body, actor):
    """Queue one non-overlapping batch. Never calls Beem from the browser."""
    body = (body or '').strip()
    sender_id = (sender_id or '').strip().upper()
    if not body or len(body) > 1000:
        raise ValidationError('Enter a message between 1 and 1,000 characters.')
    tokens = set(re.findall(r'\{([a-z_]+)\}', body, re.I))
    unsupported = tokens - {'name', 'first_name', 'phone'}
    if unsupported:
        raise ValidationError('Bulk SMS templates support only {name}, {first_name} and {phone}. Remove: ' + ', '.join(sorted(unsupported)))
    if (not provider_configured() or not getattr(settings, 'KILASI_SMS_LIVE_ENABLED', False)
            or not getattr(settings, 'KILASI_SMS_AUTOMATION_ENABLED', False)):
        raise ValidationError('Live Beem SMS is disabled or not configured.')
    if not SmsSenderProfile.objects.filter(sender_id=sender_id, is_active=True).exists():
        raise ValidationError('Choose an active Beem-approved Sender ID.')
    cap = min(BATCH_SIZE, max(1, int(getattr(settings, 'SMS_MAX_RECIPIENTS_PER_SEND', BATCH_SIZE))))

    with transaction.atomic():
        series = SmsBatchSeries.objects.select_for_update().get(pk=series_id)
        if SmsBatchEntry.objects.filter(series=series, status__in=['queued', 'sending']).exists():
            raise ValidationError('Previous batch is processing or needs provider review. Do not queue another yet.')

        # Select current contacts only; opt-outs and deactivated numbers get skipped.
        chosen = []
        for entry in SmsBatchEntry.objects.filter(series=series, status='pending').order_by('pk').select_related('contact'):
            valid = (entry.contact_id and entry.contact and entry.contact.is_active
                     and entry.contact.sms_allowed and entry.contact.normalized_phone == entry.phone)
            if not valid:
                entry.status = 'blocked'
                entry.save(update_fields=['status', 'updated_at'])
                continue
            chosen.append(entry)
            if len(chosen) >= cap:
                break
        if not chosen:
            # Persist newly discovered opt-outs instead of rolling the transaction back.
            return None, None, 0
        number = (SmsBatchEntry.objects.filter(series=series).aggregate(m=Max('batch_number'))['m'] or 0) + 1
        campaign = SmsCampaign.objects.create(
            title=f'{series.title[:115]} · Batch {number}', sender_id=sender_id, message=body,
            recipient_count=len(chosen), segment_count=sms_segments(body),
            estimated_units=0, status=SmsCampaign.Status.SENDING, created_by=actor,
            started_at=timezone.now(),
        )
        units = 0
        for entry in chosen:
            message = render_personalized_message(body, name=entry.name, phone=entry.phone)
            units += sms_segments(message)
            recipient = SmsRecipient.objects.create(
                campaign=campaign, contact=entry.contact, name=entry.name, phone=entry.phone,
                personalized_message=message,
            )
            entry.campaign = campaign
            entry.recipient = recipient
            entry.batch_number = number
            entry.status = 'queued'
            entry.save(update_fields=['campaign', 'recipient', 'batch_number', 'status', 'updated_at'])
        campaign.estimated_units = units
        campaign.save(update_fields=['estimated_units', 'updated_at'])
        return campaign, number, len(chosen)


def process_batch_outbox(limit=WORKER_LIMIT):
    """One safe submission attempt per recipient; never retry uncertain provider outcomes.

    Runs from existing Kilasi SMS timer, after transaction commit. A crash after
    the claim leaves a 'sending' audit state for manager/provider review.
    """
    if not getattr(settings, 'KILASI_SMS_LIVE_ENABLED', False) or not provider_configured():
        return dict(checked=0, accepted=0, blocked=0, uncertain=0)
    limit = min(100, max(1, int(limit)))
    ids = list(SmsBatchEntry.objects.filter(status='queued').order_by('pk')
               .values_list('pk', flat=True)[:limit])
    counts = Counter(checked=0, accepted=0, blocked=0, uncertain=0)
    touched = set()
    deadline = time.monotonic() + 50  # Keep within the existing oneshot systemd service timeout.

    for pk in ids:
        if time.monotonic() >= deadline:
            break
        with transaction.atomic():
            entry = (SmsBatchEntry.objects.select_for_update()
                     .select_related('contact', 'campaign', 'recipient').get(pk=pk))
            if entry.status != 'queued':
                continue
            rec, campaign, contact = entry.recipient, entry.campaign, entry.contact
            if not rec or not campaign:
                entry.status = 'blocked'
                entry.save(update_fields=['status', 'updated_at'])
                counts['blocked'] += 1
                continue
            allowed = (contact and contact.is_active and contact.sms_allowed and
                       contact.normalized_phone == entry.phone and
                       SmsSenderProfile.objects.filter(sender_id=campaign.sender_id, is_active=True).exists())
            if not allowed:
                entry.status = 'blocked'
                entry.save(update_fields=['status', 'updated_at'])
                rec.status = SmsRecipient.Status.FAILED
                rec.error_message = 'Opted out, inactive contact, or sender unavailable. No SMS sent.'
                rec.provider_status = 'blocked'
                rec.save(update_fields=['status', 'error_message', 'provider_status', 'updated_at'])
                counts['blocked'] += 1
                touched.add(campaign.pk)
                continue
            # Persist claim BEFORE contacting Beem. No queued retry on timeout/crash.
            entry.status = 'sending'
            entry.save(update_fields=['status', 'updated_at'])
            touched.add(campaign.pk)
        counts['checked'] += 1
        accepted, code, payload = False, 0, {}
        try:
            accepted, code, payload = _send_one_payload(campaign.sender_id, rec.personalized_message, [rec])
            if isinstance(payload, dict) and payload.get('successful') is False:
                accepted = False
        except Exception:
            # Unknown provider result. Must NEVER retry automatically.
            accepted, code = False, 0

        with transaction.atomic():
            item = SmsBatchEntry.objects.select_for_update().get(pk=pk)
            if item.status != 'sending':
                continue
            item.status = 'accepted' if accepted else 'review'
            item.save(update_fields=['status', 'updated_at'])
            rec.status = SmsRecipient.Status.SENT if accepted else SmsRecipient.Status.FAILED
            rec.sent_at = timezone.now() if accepted else None
            rec.provider_id = str(rec.pk) if accepted else ''
            rec.provider_status = 'accepted' if accepted else ('rejected' if 400 <= code < 500 else 'unknown')
            rec.error_message = '' if accepted else f'Beem HTTP {code or "unknown"}. Check provider; never auto-retry.'
            rec.save(update_fields=['status','sent_at','provider_id','provider_status','error_message','updated_at'])
            counts['accepted' if accepted else 'uncertain'] += 1

    for campaign_id in touched:
        states = Counter(SmsBatchEntry.objects.filter(campaign_id=campaign_id)
                         .values_list('status', flat=True))
        if not states['queued'] and not states['sending']:
            accepted = states['accepted']
            other = sum(v for k, v in states.items() if k != 'accepted')
            status = (SmsCampaign.Status.PARTIAL if accepted and other else
                      SmsCampaign.Status.SENT if accepted else SmsCampaign.Status.FAILED)
            SmsCampaign.objects.filter(pk=campaign_id).update(
                status=status, completed_at=timezone.now(),
                provider_response={'accepted_by_beem':accepted, 'review_or_blocked':other,
                                   'note':'Accepted is not delivered. Failed/unknown must be reconciled before another campaign.'})

    return dict(counts)


def reconcile_stalled(*, series_id, confirmed, reason):
    """Allow progress after an interrupted call, NEVER resend affected numbers.

    A manager must first inspect Beem logs and wait five minutes. Unknown
    outcomes stay permanently excluded from later batches of this series.
    """
    if not confirmed or len((reason or '').strip()) < 12:
        raise ValidationError('Confirm you checked Beem and enter a review note (12+ characters).')
    cutoff = timezone.now() - timedelta(minutes=5)
    touched = set()
    with transaction.atomic():
        series = SmsBatchSeries.objects.select_for_update().get(pk=series_id)
        stalled = list(SmsBatchEntry.objects.select_for_update().filter(
            series=series, status='sending', updated_at__lt=cutoff).select_related('recipient'))
        for entry in stalled:
            entry.status = 'review'
            entry.save(update_fields=['status', 'updated_at'])
            if entry.recipient_id:
                SmsRecipient.objects.filter(pk=entry.recipient_id).update(
                    status=SmsRecipient.Status.FAILED, provider_status='unknown',
                    error_message='Provider outcome manually marked for review. No resend.',
                    updated_at=timezone.now())
            if entry.campaign_id:
                touched.add(entry.campaign_id)
    for campaign_id in touched:
        states = Counter(SmsBatchEntry.objects.filter(campaign_id=campaign_id)
                         .values_list('status',flat=True))
        if not states['queued'] and not states['sending']:
            accepted=states['accepted']
            others=sum(v for k,v in states.items() if k!='accepted')
            final=(SmsCampaign.Status.PARTIAL if accepted and others else
                   SmsCampaign.Status.SENT if accepted else SmsCampaign.Status.FAILED)
            SmsCampaign.objects.filter(pk=campaign_id).update(status=final,completed_at=timezone.now(),
                provider_response={'note':'Interrupted submissions manually reviewed. Check Beem records.',
                                   'review_note':reason.strip()[:200]})
    return len(stalled)
