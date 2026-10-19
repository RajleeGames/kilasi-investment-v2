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
                  'cargo_details', 'scheduled_start', 'scheduled_end', 'agreed_fee', 'notes']
        widgets = {
            'scheduled_start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'scheduled_end': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        self.fields['vehicle'].queryset = Vehicle.objects.filter(status='active') if not self.instance.pk else Vehicle.objects.filter(Q(status='active') | Q(pk=self.instance.vehicle_id)).distinct()
        self.fields['driver'].queryset = Driver.objects.filter(status='active') if not self.instance.pk else Driver.objects.filter(Q(status='active') | Q(pk=self.instance.driver_id)).distinct()
        for key in ('scheduled_start', 'scheduled_end'):
            self.fields[key].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
            if self.instance.pk:
                value = getattr(self.instance, key)
                if value:
                    self.initial[key] = timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if timezone.is_aware(value) else value.strftime('%Y-%m-%dT%H:%M')

    def clean(self):
        data = super().clean()
        vehicle, driver = data.get('vehicle'), data.get('driver')
        start, end = data.get('scheduled_start'), data.get('scheduled_end')
        if not all([vehicle, driver, start, end]):
            return data
        if end <= start:
            self.add_error('scheduled_end', 'Arrival must be after departure.')
            return data
        if vehicle.status != 'active' or driver.status != 'active':
            raise forms.ValidationError('Only active drivers and vehicles may be assigned.')
        overlaps = Trip.objects.filter(status__in=['planned', 'in_progress'],
                                        scheduled_start__lt=end, scheduled_end__gt=start).filter(
                                            Q(vehicle=vehicle) | Q(driver=driver))
        if self.instance.pk:
            overlaps = overlaps.exclude(pk=self.instance.pk)
        if overlaps.exists():
            raise forms.ValidationError('The selected vehicle or driver has another trip in that time window.')
        if self.instance.pk:
            paid = self.instance.collected
            if data.get('agreed_fee') is not None and data['agreed_fee'] < paid:
                self.add_error('agreed_fee', f'Fare cannot be less than the {paid} already collected.')
        return data


class BookingForm(StyledForm):
    class Meta:
        model = Booking
        fields = ['customer_name', 'customer_phone', 'origin', 'destination', 'requested_date',
                  'cargo_details', 'quoted_amount', 'trip', 'notes']
        widgets = {'requested_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        self.fields['trip'].queryset = Trip.objects.exclude(status='cancelled')

    def clean(self):
        data = super().clean()
        trip = data.get('trip')
        # HAMA NA KILASI has one company-wide trip list.
        return data


class TripPaymentForm(StyledForm):
    class Meta:
        model = TripPayment
        fields = ['amount', 'method', 'reference', 'notes']


class TransportExpenseForm(StyledForm):
    class Meta:
        model = TransportExpense
        fields = ['trip', 'category', 'description', 'amount', 'expense_date']
        widgets = {'expense_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        self.branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        self.fields['trip'].queryset = Trip.objects.exclude(status='cancelled')
        self.fields['trip'].required = False

    def clean(self):
        data = super().clean()
        trip = data.get('trip')
        # HAMA NA KILASI has one company-wide trip list.
        return data
