from datetime import timedelta
from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import Booking, Driver, TransportExpense, Trip, TripPayment, Vehicle


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-control')


class VehicleForm(StyledForm):
    class Meta:
        model = Vehicle
        fields = ['registration', 'vehicle_type', 'make_model', 'capacity', 'status', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}

    def clean_registration(self):
        return self.cleaned_data['registration'].strip().upper()


class DriverForm(StyledForm):
    class Meta:
        model = Driver
        fields = ['full_name', 'phone', 'license_number', 'status', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}

    def clean_license_number(self):
        return (self.cleaned_data.get('license_number') or '').strip().upper() or None


class TripForm(StyledForm):
    class Meta:
        model = Trip
        fields = ['vehicle', 'driver', 'customer_name', 'customer_phone', 'origin', 'destination',
                  'cargo_details', 'scheduled_start', 'agreed_fee', 'notes']
        widgets = {
            'scheduled_start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        self.fields['vehicle'].queryset = Vehicle.objects.filter(status='active') if not self.instance.pk else Vehicle.objects.filter(Q(status='active') | Q(pk=self.instance.vehicle_id)).distinct()
        self.fields['driver'].queryset = Driver.objects.filter(status='active') if not self.instance.pk else Driver.objects.filter(Q(status='active') | Q(pk=self.instance.driver_id)).distinct()
        for key in ('scheduled_start',):
            self.fields[key].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
            if self.instance.pk:
                value = getattr(self.instance, key)
                if value:
                    self.initial[key] = timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if timezone.is_aware(value) else value.strftime('%Y-%m-%dT%H:%M')

    def clean(self):
        data = super().clean()
        vehicle, driver = data.get('vehicle'), data.get('driver')
        start = data.get('scheduled_start')
        if not all([vehicle, driver, start]):
            return data
        if vehicle.status != 'active' or driver.status != 'active':
            raise forms.ValidationError('Only active drivers and vehicles may be assigned.')
        # No new arrival estimate: block same-minute departures and protect
        # overlapping legacy trips which still have a recorded end time.
        # The existing status transition guard also prevents two active trips.
        minute_start = start.replace(second=0, microsecond=0)
        minute_end = minute_start + timedelta(minutes=1)
        overlaps = Trip.objects.filter(status__in=('planned', 'in_progress')).filter(
            Q(vehicle=vehicle) | Q(driver=driver)
        ).filter(
            Q(scheduled_start__gte=minute_start, scheduled_start__lt=minute_end) |
            Q(scheduled_start__lte=start, scheduled_end__gt=start)
        )
        if self.instance.pk:
            overlaps = overlaps.exclude(pk=self.instance.pk)
        if overlaps.exists():
            raise forms.ValidationError('This driver or vehicle has another trip at this departure or already in progress.')
        if self.instance.pk:
            paid = self.instance.collected
            if data.get('agreed_fee') is not None and data['agreed_fee'] < paid:
                self.add_error('agreed_fee', f'Fare cannot be less than the {paid} already collected.')
        return data


class BookingForm(StyledForm):
    class Meta:
        model = Booking
        fields = ['customer_name', 'customer_phone', 'origin', 'destination', 'requested_date',
                  'requested_time', 'cargo_details', 'quoted_amount', 'trip', 'notes']
        widgets = {'requested_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'requested_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
                   'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        self.fields['trip'].queryset = Trip.objects.exclude(status='cancelled')
        self.fields['requested_time'].required = not bool(self.instance.pk and self.instance.requested_time is None)
        self.fields['requested_time'].input_formats = ['%H:%M', '%H:%M:%S']

    def clean(self):
        data = super().clean()
        trip = data.get('trip')
        # HAMA NA KILASI has one company-wide trip list.
        return data


class TripPaymentForm(StyledForm):
    class Meta:
        model = TripPayment
        fields = ['amount', 'method', 'paid_at', 'reference', 'notes']
        widgets = {'paid_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['paid_at'].required = False  # Existing integrations can omit it.
        self.fields['paid_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S']
        if not self.is_bound:
            self.initial['paid_at'] = timezone.localtime().strftime('%Y-%m-%dT%H:%M')

    def clean_paid_at(self):
        value = self.cleaned_data.get('paid_at') or timezone.now()
        if value > timezone.now() + __import__('datetime').timedelta(minutes=5):
            raise forms.ValidationError('Payment time cannot be in the future.')
        return value


def expense_trips_for_day(day):
    """Candidate trips on this expense date, including multi-day active/completed work."""
    return Trip.objects.exclude(status='cancelled').filter(
        Q(scheduled_start__date=day) |
        Q(status='in_progress', scheduled_start__date__lte=day) |
        Q(status='completed', scheduled_start__date__lte=day, completed_at__date__gte=day)
    ).select_related('driver','vehicle').order_by('-scheduled_start', '-pk')


def expense_trip_label(trip):
    return (f'{trip.reference} | {trip.driver.full_name} | {trip.vehicle.registration} | '
            f'{trip.origin} → {trip.destination}')


class TransportExpenseForm(StyledForm):
    class Meta:
        model = TransportExpense
        fields = ['trip', 'category', 'description', 'amount', 'expense_date']
        widgets = {'expense_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        from datetime import date
        day_text = self.data.get('expense_date') if self.is_bound else ''
        try:
            day = date.fromisoformat(str(day_text)) if day_text else timezone.localdate()
        except ValueError:
            day = timezone.localdate()
        self.fields['trip'].queryset = expense_trips_for_day(day)
        self.fields['trip'].label_from_instance = expense_trip_label
        self.fields['trip'].widget.attrs['data-novman-search'] = '1'
        self.fields['trip'].required = False

    def clean(self):
        data = super().clean()
        trip = data.get('trip')
        # HAMA NA KILASI has one company-wide trip list.
        return data
