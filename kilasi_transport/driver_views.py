"""Private HAMA driver workspace: linked driver only, no Retail or finance permissions."""
from datetime import date
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .driver_forms import DriverTripForm
from .models import Trip, TransportAccess


def driver_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_active or request.user.is_staff or request.user.is_superuser:
            return HttpResponseForbidden('Driver account required.')
        access = getattr(request, 'transport_access', None)
        if access is None:
            access = TransportAccess.objects.select_related('driver').filter(user_id=request.user.pk).first()
        if (not access or not access.is_active or access.role != 'driver' or
                not access.driver_id or access.driver.status != 'active'):
            return HttpResponseForbidden('Active driver account required.')
        request.kilasi_driver = access.driver
        return view(request, *args, **kwargs)
    return wrapped


def _trip(request, pk):
    # URL guessing another driver's trip must not leak any details.
    return get_object_or_404(Trip.objects.select_related('vehicle', 'driver'),
                             pk=pk, driver=request.kilasi_driver)


def _dates(request):
    def parse(key):
        value = (request.GET.get(key) or '').strip()
        try:
            return date.fromisoformat(value).isoformat() if value else ''
        except ValueError:
            return ''
    start, end = parse('start'), parse('end')
    if start and end and start > end:
        start, end = end, start
    return start, end


@driver_required
def home(request):
    driver = request.kilasi_driver
    records = Trip.objects.filter(driver=driver)
    q = (request.GET.get('q') or '').strip()[:90]
    status = (request.GET.get('status') or '').strip()
    start, end = _dates(request)
    visible = records.select_related('vehicle')
    if q:
        visible = visible.filter(Q(reference__icontains=q) | Q(customer_name__icontains=q) |
                                 Q(origin__icontains=q) | Q(destination__icontains=q) |
                                 Q(vehicle__registration__icontains=q))
    if status in dict(Trip.STATES):
        visible = visible.filter(status=status)
    else:
        status = ''
    if start:
        visible = visible.filter(scheduled_start__date__gte=start)
    if end:
        visible = visible.filter(scheduled_start__date__lte=end)
    page = Paginator(visible.order_by('-scheduled_start', '-pk'), 15).get_page(request.GET.get('page'))
    options = urlencode({key: value for key, value in (
        ('q', q), ('status', status), ('start', start), ('end', end)) if value})
    return render(request, 'kilasi_transport/driver_home.html', {
        'driver': driver, 'page_obj': page, 'rows': page.object_list,
        'planned': records.filter(status='planned').count(),
        'underway': records.filter(status='in_progress').count(),
        'completed': records.filter(status='completed').count(),
        'q': q, 'status': status, 'start': start, 'end': end,
        'page_prefix': '?' + (options + '&' if options else '') + 'page=',
    })


@driver_required
def trip_new(request):
    form = DriverTripForm(request.POST or None, driver=request.kilasi_driver)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            trip = form.save(commit=False)
            trip.driver = request.kilasi_driver  # never trust submitted driver or status
            trip.created_by = request.user
            trip.branch = None
            trip.status = 'planned'
            trip.save()
        messages.success(request, f'Trip {trip.reference} saved as Planned.')
        return redirect('transport:driver_trip_detail', pk=trip.pk)
    return render(request, 'kilasi_transport/driver_trip_form.html', {
        'form': form, 'editing': False,
    })


@driver_required
def trip_edit(request, pk):
    trip = _trip(request, pk)
    if trip.status != 'planned':
        messages.error(request, 'Only planned trips can be edited. Trip history is protected.')
        return redirect('transport:driver_trip_detail', pk=trip.pk)
    form = DriverTripForm(request.POST or None, instance=trip, driver=request.kilasi_driver)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = get_object_or_404(Trip.objects.select_for_update(), pk=pk, driver=request.kilasi_driver)
            if locked.status != 'planned':
                messages.error(request, 'Trip is no longer planned. Reload its details.')
                return redirect('transport:driver_trip_detail', pk=pk)
            trip = form.save(commit=False)
            trip.driver = request.kilasi_driver
            trip.branch = locked.branch
            trip.status = locked.status
            trip.created_by = locked.created_by
            trip.save()
        messages.success(request, f'{trip.reference} updated.')
        return redirect('transport:driver_trip_detail', pk=trip.pk)
    return render(request, 'kilasi_transport/driver_trip_form.html', {
        'form': form, 'editing': True, 'trip': trip,
    })


@driver_required
def trip_detail(request, pk):
    return render(request, 'kilasi_transport/driver_trip_detail.html', {
        'trip': _trip(request, pk),
    })


@driver_required
@require_POST
def trip_status(request, pk):
    # Drivers can manage only trips currently assigned to their own Driver PK.
    action = (request.POST.get('status') or '').strip()
    reason = (request.POST.get('reason') or '').strip()
    allowed = {
        'planned': {'in_progress', 'cancelled'},
        'in_progress': {'completed', 'cancelled'},
    }

    with transaction.atomic():
        trip = get_object_or_404(
            Trip.objects.select_for_update(), pk=pk, driver=request.kilasi_driver
        )
        if action not in allowed.get(trip.status, set()):
            messages.error(request, 'This trip cannot be changed from its current status.')
        elif action == 'cancelled':
            if not 5 <= len(reason) <= 200:
                messages.error(request, 'Enter a cancellation reason of 5 to 200 characters.')
            elif trip.payments.filter(is_voided=False).exists():
                messages.error(request, 'Trip has payments. A manager must reconcile/refund and void them first.')
            elif trip.bookings.filter(status__in=['pending', 'confirmed', 'completed']).exists():
                messages.error(request, 'Linked bookings must be reconciled by a manager before cancellation.')
            elif trip.transport_expenses.filter(is_voided=False).exists():
                messages.error(request, 'Trip has posted expenses. A manager must review them before cancellation.')
            else:
                # Status is a soft-void: retain the trip, actor and explanation.
                trip.status = 'cancelled'
                trip.cancel_reason = reason
                trip.cancelled_at = timezone.now()
                trip.cancelled_by = request.user
                trip.save(update_fields=['status', 'cancel_reason', 'cancelled_at', 'cancelled_by'])
                messages.success(request, 'Trip cancelled. The original record and audit trail were preserved.')
        elif action == 'in_progress':
            trip.status = action
            trip.save(update_fields=['status'])
            messages.success(request, 'Trip started. SMS is queued if the approved automation rule is enabled.')
        elif action == 'completed':
            trip.status = action
            trip.completed_at = timezone.now()
            trip.save(update_fields=['status', 'completed_at'])
            messages.success(request, 'Trip completed. SMS is queued if the approved automation rule is enabled.')

    return redirect('transport:driver_trip_detail', pk=pk)
