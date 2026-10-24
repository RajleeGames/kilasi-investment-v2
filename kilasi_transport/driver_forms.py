"""Driver-owned trip form, using existing Kilasi form controls and model validation."""
from datetime import timedelta
from django import forms
from django.db.models import Q
from django.utils import timezone
from .forms import StyledForm
from .models import Trip, Vehicle


class DriverTripForm(StyledForm):
    class Meta:
        model = Trip
        fields = ('vehicle', 'customer_name', 'customer_phone', 'origin',
                  'destination', 'cargo_details', 'scheduled_start',
                  'agreed_fee', 'notes')
        widgets = {
            'scheduled_start': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, driver, **kwargs):
        self.driver = driver
        super().__init__(*args, **kwargs)
        self.fields['vehicle'].queryset = Vehicle.objects.filter(
            Q(status='active') | Q(pk=self.instance.vehicle_id)
        ).distinct() if self.instance.pk else Vehicle.objects.filter(status='active')
        for key in ('scheduled_start',):
            self.fields[key].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
            if self.instance.pk:
                value = getattr(self.instance, key)
                if value:
                    self.initial[key] = timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if timezone.is_aware(value) else value.strftime('%Y-%m-%dT%H:%M')

    def clean(self):
        data = super().clean()
        vehicle, start = data.get('vehicle'), data.get('scheduled_start')
        if not all((vehicle, start)):
            return data
        if vehicle.status != 'active' or self.driver.status != 'active':
            raise forms.ValidationError('Vehicle and assigned driver must both be active.')
        # No guessed duration: protect same-minute starts and old trips
        # whose historical end time is known. In-progress conflicts are also
        # enforced when a driver actually starts a trip.
        minute_start = start.replace(second=0, microsecond=0)
        minute_end = minute_start + timedelta(minutes=1)
        conflict = Trip.objects.filter(status__in=('planned', 'in_progress')).filter(
            Q(vehicle=vehicle) | Q(driver=self.driver)
        ).filter(
            Q(scheduled_start__gte=minute_start, scheduled_start__lt=minute_end) |
            Q(scheduled_start__lte=start, scheduled_end__gt=start)
        )
        if self.instance.pk:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            raise forms.ValidationError('This vehicle or driver already has a trip during these hours.')
        return data
