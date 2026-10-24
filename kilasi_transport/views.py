"""Single HAMA NA KILASI department; finance kept separate from Retail.

Transport-only user roles and granular permissions are scheduled for a later
phase. Do not loosen these views to ordinary retail cashiers.
"""
from datetime import datetime, timedelta
from .list_filters import filtered_page  # KILASI_TRANSPORT_FILTERS_V1
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, Count, F, Value, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .permissions import transport_manager_required as admin_required, transport_staff_required
from types import SimpleNamespace
from .forms import BookingForm, DriverForm, TransportExpenseForm, TripForm, TripPaymentForm, VehicleForm, expense_trips_for_day, expense_trip_label
from .staff_forms import StaffBookingForm
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_GET
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


def _finance(branch, start, end, vehicle_id=None, driver_id=None):
    completed = Trip.objects.filter(status='completed', completed_at__date__range=(start, end))
    payments = TripPayment.objects.filter(is_voided=False, paid_at__date__range=(start, end))
    expenses = TransportExpense.objects.filter(is_voided=False, expense_date__range=(start, end))
    trip_scope={}
    if vehicle_id: trip_scope['vehicle_id']=vehicle_id
    if driver_id: trip_scope['driver_id']=driver_id
    if trip_scope:
        completed=completed.filter(**trip_scope)
        payments=payments.filter(**{'trip__'+k:v for k,v in trip_scope.items()})
        expenses=expenses.filter(**{'trip__'+k:v for k,v in trip_scope.items()})
    earned = _sum(completed, 'agreed_fee')
    collected = _sum(payments, 'amount')
    spent = _sum(expenses, 'amount')
    lifetime_due = sum((trip.balance for trip in Trip.objects.filter(status='completed', **trip_scope).prefetch_related('payments')), ZERO)
    return {'earned': earned, 'collected': collected, 'spent': spent,
            'cash_net': collected - spent, 'period_margin': earned - spent,
            'outstanding': lifetime_due, 'completed_count': completed.count(),
            'payment_count': payments.count(), 'expense_count': expenses.count()}


def _driver_performance(start, end, vehicle_id=None, driver_id=None):
    """Period charges on completed trips; payments/expenses by their actual posted dates.

    Remaining balance concerns completed trips in the period, including installments
    collected later. Never treat agreed fee as cash received.
    """
    scope = {}
    if vehicle_id: scope['vehicle_id'] = vehicle_id
    if driver_id: scope['driver_id'] = driver_id
    completed = Trip.objects.filter(status='completed', completed_at__date__range=(start, end), **scope)
    payment_filters = {'trip__'+k:v for k,v in scope.items()}
    payment_data = (TripPayment.objects.filter(is_voided=False, paid_at__date__range=(start, end), **payment_filters)
                    .values('trip__driver_id').annotate(money=Sum('amount')))
    expense_data = (TransportExpense.objects.filter(is_voided=False, trip__isnull=False,
                    expense_date__range=(start, end), **payment_filters)
                    .values('trip__driver_id').annotate(money=Sum('amount')))
    completed_data = completed.values('driver_id').annotate(count=Count('pk'), charges=Sum('agreed_fee'))
    totals = {}
    def slot(pk):
        return totals.setdefault(pk, {'trip_count':0,'total_charges':ZERO,
                                     'collected':ZERO,'expenses':ZERO,'balance':ZERO})
    for row in completed_data:
        r=slot(row['driver_id']);r['trip_count']=row['count'];r['total_charges']=row['charges'] or ZERO
    for row in payment_data:slot(row['trip__driver_id'])['collected']=row['money'] or ZERO
    for row in expense_data:slot(row['trip__driver_id'])['expenses']=row['money'] or ZERO
    for trip in completed.prefetch_related('payments'):
        slot(trip.driver_id)['balance'] += trip.balance
    names=dict(Driver.objects.filter(pk__in=totals).values_list('pk','full_name'))
    result=[]
    for pk, metric in totals.items():
        result.append({'driver_name':names.get(pk, f'Driver #{pk}'), **metric,
                       'net_cash':metric['collected']-metric['expenses']})
    return sorted(result,key=lambda r:(-r['trip_count'],-r['total_charges'],r['driver_name']))


def _overdue_bookings():
    # Never manufacture an overdue hour for historical bookings with no time.
    today = timezone.localdate()
    candidates=Booking.objects.filter(status__in=('pending','confirmed')).filter(
        Q(requested_date__lt=today) | Q(requested_date=today, requested_time__isnull=False)
    ).select_related('trip')
    return [b for b in candidates if b.is_overdue]


@transport_staff_required
def dashboard(request):
    branch = None
    start, end = _period(request)
    member = getattr(request, 'transport_access', None)
    if member and member.role == 'staff':
        overdue=_overdue_bookings()
        return render(request, 'kilasi_transport/staff_dashboard.html', {
            'branch': SimpleNamespace(name='HAMA NA KILASI'),
            'open_trips': Trip.objects.filter(status__in=['planned','in_progress']).count(),
            'pending_bookings': Booking.objects.filter(status='pending').count(),
            'overdue_count':len(overdue), 'overdue_bookings':overdue[:8],
            'vehicle_count': Vehicle.objects.filter(status='active').count(),
            'recent_trips': Trip.objects.filter().select_related('vehicle')[:12],
        })
    selected_vehicle=(request.GET.get('vehicle') or '').strip()
    selected_driver=(request.GET.get('driver') or '').strip()
    vehicle_id=int(selected_vehicle) if selected_vehicle.isdecimal() and len(selected_vehicle)<12 and Vehicle.objects.filter(pk=selected_vehicle).exists() else None
    driver_id=int(selected_driver) if selected_driver.isdecimal() and len(selected_driver)<12 and Driver.objects.filter(pk=selected_driver).exists() else None
    filtered=bool(vehicle_id or driver_id)
    summary = _finance(branch, start, end, vehicle_id, driver_id)
    all_completed=Trip.objects.filter(status='completed', completed_at__date__range=(start,end))
    if vehicle_id: all_completed=all_completed.filter(vehicle_id=vehicle_id)
    if driver_id: all_completed=all_completed.filter(driver_id=driver_id)
    ranks=_driver_performance(start,end,vehicle_id,driver_id)
    overdue=_overdue_bookings()
    recent_scope=Trip.objects.select_related('vehicle','driver')
    if vehicle_id: recent_scope=recent_scope.filter(vehicle_id=vehicle_id)
    if driver_id: recent_scope=recent_scope.filter(driver_id=driver_id)
    return render(request, 'kilasi_transport/dashboard.html', {
        **_ctx(request, **summary), 'start': start, 'end': end,
        'vehicle_count': Vehicle.objects.filter(status='active').count(),
        'driver_count': Driver.objects.filter(status='active').count(),
        'open_trips': Trip.objects.filter(status__in=['planned', 'in_progress']).count(),
        'pending_bookings': Booking.objects.filter(status='pending').count(),
        'recent_trips': recent_scope[:12],
        'vehicle_choices': Vehicle.objects.order_by('registration'),
        'driver_choices': Driver.objects.order_by('full_name'),
        'selected_vehicle': str(vehicle_id or ''), 'selected_driver': str(driver_id or ''),
        'scoped_finance': filtered, 'driver_ranks': ranks,
        'overdue_count': len(overdue), 'overdue_bookings': overdue[:8],
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
    payment_filter = (request.GET.get('payment') or '').strip()
    if payment_filter in ('paid', 'partial', 'unpaid'):
        qs = qs.annotate(_paid_total=Coalesce(
            Sum('payments__amount', filter=Q(payments__is_voided=False)),
            Value(ZERO), output_field=DecimalField(max_digits=14,decimal_places=2)))
        if payment_filter == 'paid':
            qs=qs.filter(agreed_fee__gt=0, _paid_total__gte=F('agreed_fee'))
        elif payment_filter == 'partial':
            qs=qs.filter(agreed_fee__gt=F('_paid_total'), _paid_total__gt=0)
        else:
            qs=qs.filter(agreed_fee__gt=0, _paid_total=0)
    else:
        payment_filter = ''
    data = filtered_page(
        request, qs, date_field='scheduled_start__date',
        search_fields=(
            'reference', 'customer_name', 'customer_phone', 'origin',
            'destination', 'vehicle__registration', 'driver__full_name',
        ),
        statuses=Trip.STATES, vehicle_field='vehicle_id',driver_field='driver_id',
    )
    from kilasi_sms.models import KilasiSmsAutomaticJob
    from kilasi_sms.automation import can_manually_retry
    trip_ids=[t.pk for t in data['rows']]
    jobs=KilasiSmsAutomaticJob.objects.filter(branch__isnull=True,
        event_key__startswith='transport.trip_',source_id__in=trip_ids).order_by('pk')
    by_trip={}
    for job in jobs:
        job.can_retry_from_page=can_manually_retry(job)
        by_trip.setdefault(job.source_id,[]).append(job)
    for trip in data['rows']:
        trip.sms_jobs=by_trip.get(trip.pk,[])
    data.update(vehicle_choices=Vehicle.objects.order_by('registration'),
                driver_choices=Driver.objects.order_by('full_name'),
                payment_filter=payment_filter, payment_filter_choices=(('paid','Fully paid'),('partial','Partially paid'),('unpaid','Unpaid')))

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
        elif action == 'in_progress' and Trip.objects.filter(status='in_progress').exclude(pk=trip.pk).filter(
                Q(vehicle_id=trip.vehicle_id) | Q(driver_id=trip.driver_id)).exists():
            messages.error(request, 'This driver or vehicle already has a trip in progress.')
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
    for b in data['rows']:
        b.overdue_display = b.is_overdue
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
@require_GET
def expense_trip_options(request):
    from datetime import date
    value=(request.GET.get('date') or '').strip()
    try:
        day=date.fromisoformat(value)
    except ValueError:
        return JsonResponse({'error':'Choose a valid expense date.'},status=400)
    rows=expense_trips_for_day(day)[:2000]
    return JsonResponse({'trips':[{'id':t.pk,'label':expense_trip_label(t)} for t in rows]})


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
