"""Single HAMA NA KILASI department; finance kept separate from Retail.

Transport-only user roles and granular permissions are scheduled for a later
phase. Do not loosen these views to ordinary retail cashiers.
"""
from datetime import datetime, timedelta
from .list_filters import filtered_page  # KILASI_TRANSPORT_FILTERS_V1
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .permissions import transport_manager_required as admin_required, transport_staff_required
from types import SimpleNamespace
from .forms import BookingForm, DriverForm, TransportExpenseForm, TripForm, TripPaymentForm, VehicleForm
from .staff_forms import StaffBookingForm
from django.http import HttpResponseForbidden
from .models import Booking, Driver, TransportExpense, Trip, TripPayment, Vehicle, ZERO


def _ctx(request, **kwargs):
    return {'branch': SimpleNamespace(name='HAMA NA KILASI'), **kwargs}


def _period(request):
    today = timezone.localdate()
    def date_arg(key, default):
        try:
            return datetime.strptime(request.GET.get(key, ''), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return default
    start = date_arg('start', today.replace(day=1))
    end = date_arg('end', today)
    if start > end:
        start, end = end, start
    if (end - start).days > 365:
        start = end - timedelta(days=365)
    return start, end


def _sum(queryset, field):
    return queryset.aggregate(v=Sum(field))['v'] or ZERO


def _finance(branch, start, end):
    completed = Trip.objects.filter(status='completed', completed_at__date__range=(start, end))
    payments = TripPayment.objects.filter(is_voided=False, paid_at__date__range=(start, end))
    expenses = TransportExpense.objects.filter(is_voided=False, expense_date__range=(start, end))
    earned = _sum(completed, 'agreed_fee')
    collected = _sum(payments, 'amount')
    spent = _sum(expenses, 'amount')
    lifetime_due = sum((trip.balance for trip in Trip.objects.filter(status='completed').prefetch_related('payments')), ZERO)
    return {'earned': earned, 'collected': collected, 'spent': spent,
            'cash_net': collected - spent, 'period_margin': earned - spent,
            'outstanding': lifetime_due, 'completed_count': completed.count(),
            'payment_count': payments.count(), 'expense_count': expenses.count()}


@transport_staff_required
def dashboard(request):
    branch = None
    start, end = _period(request)
    member = getattr(request, 'transport_access', None)
    if member and member.role == 'staff':
        return render(request, 'kilasi_transport/staff_dashboard.html', {
            'branch': SimpleNamespace(name='HAMA NA KILASI'),
            'open_trips': Trip.objects.filter(status__in=['planned','in_progress']).count(),
            'pending_bookings': Booking.objects.filter(status='pending').count(),
            'vehicle_count': Vehicle.objects.filter(status='active').count(),
            'recent_trips': Trip.objects.filter().select_related('vehicle')[:12],
        })
    summary = _finance(branch, start, end)
    return render(request, 'kilasi_transport/dashboard.html', {
        **_ctx(request, **summary), 'start': start, 'end': end,
        'vehicle_count': Vehicle.objects.filter(status='active').count(),
        'driver_count': Driver.objects.filter(status='active').count(),
        'open_trips': Trip.objects.filter(status__in=['planned', 'in_progress']).count(),
        'pending_bookings': Booking.objects.filter(status='pending').count(),
        'recent_trips': Trip.objects.filter().select_related('vehicle', 'driver')[:12],
        'recent_bookings': Booking.objects.filter()[:8],
    })


@transport_staff_required
def vehicles(request):
    qs = Vehicle.objects.all().order_by('registration', 'id')
    data = filtered_page(
        request, qs, date_field='created_at__date',
        search_fields=('registration', 'make_model', 'capacity'),
        statuses=Vehicle.STATES,
    )
    return render(request, 'kilasi_transport/vehicles.html', _ctx(request, **data))


@admin_required
def vehicle_edit(request, pk=None):
    obj = get_object_or_404(Vehicle, pk=pk) if pk else None
    form = VehicleForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        vehicle = form.save()
        messages.success(request, f'{vehicle.registration} saved.')
        return redirect('transport:vehicles')
    return render(request, 'kilasi_transport/edit.html', _ctx(request, form=form, name='vehicle', obj=obj, back='transport:vehicles'))


@transport_staff_required
def drivers(request):
    qs = Driver.objects.all().order_by('full_name', 'id')
    data = filtered_page(
        request, qs, date_field='created_at__date',
        search_fields=('full_name', 'phone', 'license_number'),
        statuses=Driver.STATES,
    )
    return render(request, 'kilasi_transport/drivers.html', _ctx(request, **data))


@admin_required
def driver_edit(request, pk=None):
    obj = get_object_or_404(Driver, pk=pk) if pk else None
    form = DriverForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        driver = form.save()
        messages.success(request, f'{driver.full_name} saved.')
        return redirect('transport:drivers')
    return render(request, 'kilasi_transport/edit.html', _ctx(request, form=form, name='driver', obj=obj, back='transport:drivers'))


@transport_staff_required
def trips(request):
    qs = Trip.objects.select_related('vehicle', 'driver').order_by('-scheduled_start', '-pk')
    data = filtered_page(
        request, qs, date_field='scheduled_start__date',
        search_fields=(
            'reference', 'customer_name', 'customer_phone', 'origin',
            'destination', 'vehicle__registration', 'driver__full_name',
        ),
        statuses=Trip.STATES,
    )
    return render(request, 'kilasi_transport/trips.html', _ctx(request, **data))


@admin_required
def trip_edit(request, pk=None):
    branch = None
    obj = get_object_or_404(Trip, pk=pk, ) if pk else None
    if obj and obj.status != 'planned':
        messages.error(request, 'Only planned trips may be edited. Completed trip history is locked.')
        return redirect('transport:trip_detail', pk=obj.pk)
    form = TripForm(request.POST or None, instance=obj, )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            trip = form.save(commit=False)
            if not trip.pk: trip.branch = None
            if not trip.pk:
                trip.created_by = request.user
            trip.save()
        messages.success(request, f'{trip.reference} saved.')
        return redirect('transport:trip_detail', pk=trip.pk)
    return render(request, 'kilasi_transport/edit.html', _ctx(request, form=form, name='trip', obj=obj, back='transport:trips'))


@transport_staff_required
def trip_detail(request, pk):
    trip = get_object_or_404(Trip.objects.select_related('vehicle', 'driver').prefetch_related('payments'),
                             pk=pk)
    member = getattr(request, 'transport_access', None)
    if member and member.role == 'staff':
        return render(request, 'kilasi_transport/staff_trip_detail.html', _ctx(request, trip=trip))
    return render(request, 'kilasi_transport/trip_detail.html', _ctx(
        request, trip=trip, payment_form=TripPaymentForm(),
        payments=trip.payments.select_related('created_by').all(),
        expenses=trip.transport_expenses.filter(is_voided=False),
        bookings=trip.bookings.all(),
    ))


@admin_required
@require_POST
def trip_status(request, pk):
    action = (request.POST.get('status') or '').strip()
    reason = (request.POST.get('reason') or '').strip()
    allowed = {
        'planned': {'in_progress', 'cancelled'},
        'in_progress': {'completed', 'cancelled'},
    }
    with transaction.atomic():
        trip = get_object_or_404(Trip.objects.select_for_update(), pk=pk)
        if action not in allowed.get(trip.status, set()):
            messages.error(request, 'This trip cannot be changed from its current status.')
        elif action == 'cancelled':
            if len(reason) < 5 or len(reason) > 200:
                messages.error(request, 'A cancellation reason of 5 to 200 characters is required.')
            elif trip.payments.filter(is_voided=False).exists():
                messages.error(request, 'Trip has active payments. Reconcile/refund and void them before cancelling.')
            elif trip.bookings.filter(status__in=['pending', 'confirmed', 'completed']).exists():
                messages.error(request, 'Cancel linked active bookings before cancelling this trip.')
            else:
                trip.status = 'cancelled'
                trip.cancel_reason = reason
                trip.cancelled_at = timezone.now()
                trip.cancelled_by = request.user
                trip.save(update_fields=['status', 'cancel_reason', 'cancelled_at', 'cancelled_by'])
                messages.success(request, f'{trip.reference} cancelled. Original record and audit trail retained.')
        else:
            trip.status = action
            if action == 'completed':
                trip.completed_at = timezone.now()
                trip.save(update_fields=['status', 'completed_at'])
            else:
                trip.save(update_fields=['status'])
            messages.success(request, f'Trip marked {trip.get_status_display().lower()}.')
    return redirect('transport:trip_detail', pk=pk)


@admin_required
@require_POST
def payment_new(request, pk):
    branch = None
    with transaction.atomic():
        trip = get_object_or_404(Trip.objects.select_for_update(), pk=pk, )
        form = TripPaymentForm(request.POST)
        if trip.status not in ('in_progress', 'completed'):
            messages.error(request, 'Only started or completed trips can receive payments.')
        elif form.is_valid():
            amount = form.cleaned_data['amount']
            if amount > 0 and amount <= trip.balance:
                payment = form.save(commit=False)
                payment.trip = trip
                payment.created_by = request.user
                payment.save()
                messages.success(request, f'{amount} TZS received against {trip.reference}.')
            else:
                messages.error(request, f'Payment must be positive and no more than the outstanding {trip.balance} TZS.')
        else:
            messages.error(request, 'Payment was not recorded. Check amount and method.')
    return redirect('transport:trip_detail', pk=pk)


@admin_required
@require_POST
def payment_void(request, pk):
    branch = None
    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, 'A reason is required to void a payment.')
        payment = get_object_or_404(TripPayment, pk=pk, )
        return redirect('transport:trip_detail', pk=payment.trip_id)
    with transaction.atomic():
        payment = get_object_or_404(TripPayment.objects.select_for_update(), pk=pk, )
        if payment.is_voided:
            messages.error(request, 'Payment already voided.')
        else:
            payment.is_voided = True
            payment.voided_at = timezone.now()
            payment.voided_by = request.user
            payment.void_reason = reason[:200]
            payment.save(update_fields=['is_voided', 'voided_at', 'voided_by', 'void_reason'])
            messages.success(request, 'Payment voided with audit history retained.')
    return redirect('transport:trip_detail', pk=payment.trip_id)


@transport_staff_required
def bookings(request):
    qs = Booking.objects.select_related('trip').order_by('-requested_date', '-pk')
    data = filtered_page(
        request, qs, date_field='requested_date',
        search_fields=('reference', 'customer_name', 'customer_phone', 'origin', 'destination'),
        statuses=Booking.STATES,
    )
    return render(request, 'kilasi_transport/bookings.html', _ctx(request, **data))


@transport_staff_required
def booking_edit(request, pk=None):
    branch = None
    obj = get_object_or_404(Booking, pk=pk, ) if pk else None
    if obj and obj.status not in ('pending', 'confirmed'):
        messages.error(request, 'Completed or cancelled bookings cannot be edited.')
        return redirect('transport:bookings')
    member = getattr(request, 'transport_access', None)
    if member and member.role == 'staff' and obj is not None:
        return HttpResponseForbidden('Staff may submit new bookings, but only managers can edit an existing booking.')
    form_class = StaffBookingForm if member and member.role == 'staff' else BookingForm
    form = form_class(request.POST or None, instance=obj, )
    if request.method == 'POST' and form.is_valid():
        booking = form.save(commit=False)
        if not booking.pk: booking.branch = None
        if not booking.pk:
            booking.created_by = request.user
        booking.save()
        messages.success(request, f'{booking.reference} saved.')
        return redirect('transport:bookings')
    return render(request, 'kilasi_transport/edit.html', _ctx(request, form=form, name='booking', obj=obj, back='transport:bookings'))


@admin_required
@require_POST
def booking_status(request, pk):
    branch = None
    action = (request.POST.get('status') or '').strip()
    allowed = {'pending': {'confirmed', 'cancelled'}, 'confirmed': {'completed', 'cancelled'}}
    with transaction.atomic():
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=pk, )
        if action not in allowed.get(booking.status, set()):
            messages.error(request, 'Invalid booking status transition.')
        elif action == 'completed' and (not booking.trip_id or booking.trip.status != 'completed'):
            messages.error(request, 'Assign a completed trip before completing the booking.')
        else:
            booking.status = action
            booking.save(update_fields=['status'])
            messages.success(request, f'Booking marked {booking.get_status_display().lower()}.')
    return redirect('transport:bookings')


@admin_required
def expenses(request):
    form = TransportExpenseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.branch = None
            item.created_by = request.user
            item.save()
        messages.success(request, 'Transport expense recorded.')
        return redirect('transport:expenses')
    qs = TransportExpense.objects.select_related('trip', 'created_by').order_by('-expense_date', '-pk')
    data = filtered_page(
        request, qs, date_field='expense_date',
        search_fields=('description', 'trip__reference', 'created_by__username'),
        statuses=(('posted', 'Posted'), ('voided', 'Voided')),
        state_field='is_voided',
        category_choices=TransportExpense.CATEGORIES,
    )
    return render(request, 'kilasi_transport/expenses.html', _ctx(request, form=form, **data))


@admin_required
@require_POST
def expense_void(request, pk):
    branch = None
    reason = (request.POST.get('reason') or '').strip()
    with transaction.atomic():
        item = get_object_or_404(TransportExpense.objects.select_for_update(), pk=pk, )
        if item.is_voided:
            messages.error(request, 'Expense already voided.')
        elif not reason:
            messages.error(request, 'Reason is required to void an expense.')
        else:
            item.is_voided = True
            item.voided_at = timezone.now()
            item.voided_by = request.user
            item.void_reason = reason[:200]
            item.save(update_fields=['is_voided', 'voided_at', 'voided_by', 'void_reason'])
            messages.success(request, 'Transport expense voided; original entry retained for audit.')
    return redirect('transport:expenses')


@admin_required
def reports(request):
    branch = None
    start, end = _period(request)
    summary = _finance(branch, start, end)
    payments = TripPayment.objects.filter(is_voided=False,
                                          paid_at__date__range=(start, end)).select_related('trip')[:80]
    expenses_qs = TransportExpense.objects.filter(is_voided=False,
                                                  expense_date__range=(start, end)).select_related('trip')[:80]
    completed = Trip.objects.filter(status='completed', completed_at__date__range=(start, end))[:80]
    return render(request, 'kilasi_transport/reports.html', _ctx(
        request, **summary, start=start, end=end, payments=payments,
        expenses=expenses_qs, completed_trips=completed,
    ))
