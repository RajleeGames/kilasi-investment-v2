"""Opt-in 500-recipient batch campaign screens for SMS Center admins only."""
from collections import Counter

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.decorators import admin_required
from .batch_service import BATCH_SIZE, make_series, prepare_next, reconcile_stalled
from .models import SmsBatchSeries, SmsBatchEntry, SmsContact, SmsSenderProfile, SmsTemplate
from .services import get_beem_balance, provider_configured


@admin_required
@require_http_methods(['GET','POST'])
def batch_list(request):
    if request.method == 'POST':
        if request.POST.get('permission_reviewed') != 'on':
            messages.error(request, 'Review permission to contact the saved audience before creating a series.')
        else:
            try:
                series = make_series(actor=request.user,
                                     title=request.POST.get('title'),
                                     group=request.POST.get('group_name'))
                messages.success(request, 'Audience snapshot created. No SMS sent.')
                return redirect('sms_center:batch_detail', pk=series.pk)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
    groups = list(SmsContact.objects.filter(is_active=True, sms_allowed=True)
                  .exclude(group_name='').values_list('group_name', flat=True)
                  .distinct().order_by('group_name'))
    qs = SmsBatchSeries.objects.select_related('created_by').annotate(
        audience_count=Count('entries'),
        remaining=Count('entries', filter=Q(entries__status='pending'))).order_by('-pk')
    return render(request, 'sms_center/batches.html', {
        'groups': groups,
        'series_list': Paginator(qs, 20).get_page(request.GET.get('page')),
        'batch_size': BATCH_SIZE,
    })


@admin_required
@require_http_methods(['GET'])
def batch_detail(request, pk):
    series = get_object_or_404(SmsBatchSeries.objects.select_related('created_by'), pk=pk)
    states = Counter(dict(SmsBatchEntry.objects.filter(series=series).values('status')
                          .annotate(n=Count('pk')).values_list('status','n')))
    # Counter(dict(...)) preserves exact values, not frequencies.
    pending = states['pending']
    inflight = states['queued'] + states['sending']
    eligible_now = list(SmsBatchEntry.objects.filter(series=series,status='pending')
                        .select_related('contact').order_by('pk')[:BATCH_SIZE])
    preview = eligible_now[:12]
    batches = (SmsBatchEntry.objects.filter(series=series,campaign__isnull=False)
               .values('batch_number','campaign_id','campaign__status','campaign__title')
               .annotate(size=Count('pk')).order_by('batch_number')[:100])
    return render(request, 'sms_center/batch_detail.html', {
        'series': series, 'states': states, 'total': sum(states.values()), 'pending': pending,
        'inflight': inflight, 'preview': preview, 'batches': batches,
        'can_queue': bool(pending and not inflight), 'batch_size':BATCH_SIZE,
        'templates': SmsTemplate.objects.filter(is_active=True).order_by('name'),
        'senders': SmsSenderProfile.objects.filter(is_active=True).order_by('-is_default','sender_id'),
        'provider_ready': provider_configured(),
        'balance': get_beem_balance(),
    })


@admin_required
@require_POST
def queue_batch(request, pk):
    if request.POST.get('confirm_batch') != 'on':
        messages.error(request, 'Review and confirm permission and the message before queuing this batch.')
        return redirect('sms_center:batch_detail', pk=pk)
    try:
        campaign, number, count = prepare_next(
            series_id=pk, sender_id=request.POST.get('sender_id'),
            body=request.POST.get('message'), actor=request.user)
        if count == 0:
            messages.warning(request, 'No eligible contacts remained; skipped newly opted-out numbers. No SMS queued.')
        else:
            messages.success(request, f'Batch {number} queued with {count} unique numbers. '
                             'The existing SMS timer processes it safely; check status before the next batch.')
    except SmsBatchSeries.DoesNotExist:
        messages.error(request, 'Batch series not found.')
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect('sms_center:batch_detail', pk=pk)


@admin_required
@require_POST
def batch_review(request, pk):
    try:
        count = reconcile_stalled(series_id=pk,
             confirmed=request.POST.get('beem_checked') == 'on',
             reason=request.POST.get('reason', ''))
        if count:
            messages.warning(request, f'{count} stale provider submissions marked for review. They will NOT be resent.')
        else:
            messages.info(request, 'No submissions older than five minutes need review.')
    except SmsBatchSeries.DoesNotExist:
        messages.error(request, 'Batch series not found.')
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect('sms_center:batch_detail', pk=pk)
