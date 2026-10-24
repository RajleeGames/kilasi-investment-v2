"""Kilasi Transport department. Independent from the retail sale/stock ledger."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0.00')


class Vehicle(models.Model):
    TYPES = [('truck', 'Truck'), ('pickup', 'Pickup'), ('van', 'Van'), ('car', 'Car'), ('other', 'Other')]
    STATES = [('active', 'Active'), ('maintenance', 'Maintenance'), ('inactive', 'Inactive')]
    registration = models.CharField(max_length=24, unique=True)
    vehicle_type = models.CharField(max_length=16, choices=TYPES, default='truck')
    make_model = models.CharField(max_length=120)
    capacity = models.CharField(max_length=80, blank=True, help_text='Example: 7 tonnes or 14 seats')
    status = models.CharField(max_length=16, choices=STATES, default='active')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['registration']

    def __str__(self):
        return self.registration


class Driver(models.Model):
    STATES = [('active', 'Active'), ('inactive', 'Inactive')]
    full_name = models.CharField(max_length=140)
    phone = models.CharField(max_length=40, blank=True)
    license_number = models.CharField(max_length=70, unique=True, null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATES, default='active')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['full_name', 'pk']

    def __str__(self):
        return self.full_name


class Trip(models.Model):
    STATES = [
        ('planned', 'Planned'), ('in_progress', 'In progress'),
        ('completed', 'Completed'), ('cancelled', 'Cancelled'),
    ]
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_trips', null=True, blank=True)
    reference = models.CharField(max_length=30, unique=True, blank=True)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='trips')
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name='trips')
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=40, blank=True)
    origin = models.CharField(max_length=180)
    destination = models.CharField(max_length=180)
    cargo_details = models.CharField(max_length=240, blank=True)
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField(null=True, blank=True, help_text="Legacy optional arrival time; new trips have no estimated end time.")
    agreed_fee = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    status = models.CharField(max_length=16, choices=STATES, default='planned')
    completed_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name='kilasi_trips_cancelled',
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='kilasi_trips_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-scheduled_start', '-pk']
        indexes = [models.Index(fields=['branch', 'status', 'scheduled_start'])]

    def clean(self):
        super().clean()
        if self.scheduled_start and self.scheduled_end and self.scheduled_end <= self.scheduled_start:
            raise ValidationError({'scheduled_end': 'Scheduled arrival must be after departure.'})
        if self.status == 'completed' and not self.completed_at:
            raise ValidationError('A completed trip needs a completion timestamp.')

    def save(self, *args, **kwargs):
        creating = not self.pk
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f'KI-TR-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)

    @property
    def collected(self):
        return self.payments.filter(is_voided=False).aggregate(v=Sum('amount'))['v'] or ZERO

    @property
    def balance(self):
        return max(ZERO, self.agreed_fee - self.collected)

    @property
    def payment_state(self):
        if self.status == 'cancelled':
            return 'Cancelled'
        if self.agreed_fee <= ZERO:
            return 'No charge'
        if self.balance <= ZERO:
            return 'Fully paid'
        if self.collected > ZERO:
            return 'Partially paid'
        return 'Unpaid'

    @property
    def fully_paid_at(self):
        """Actual date/time the accumulated non-voided installments covered the fare."""
        if self.agreed_fee <= ZERO or self.balance > ZERO:
            return None
        running = ZERO
        for p in self.payments.filter(is_voided=False).order_by('paid_at', 'pk'):
            running += p.amount
            if running >= self.agreed_fee:
                return p.paid_at
        return None

    def __str__(self):
        return self.reference or f'Trip {self.pk}'


class Booking(models.Model):
    STATES = [('pending', 'Pending'), ('confirmed', 'Confirmed'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_bookings', null=True, blank=True)
    reference = models.CharField(max_length=30, unique=True, blank=True)
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=40, blank=True)
    origin = models.CharField(max_length=180)
    destination = models.CharField(max_length=180)
    requested_date = models.DateField()
    requested_time = models.TimeField(null=True, blank=True, help_text="Scheduled service time; may be unknown on older records.")
    cargo_details = models.CharField(max_length=240, blank=True)
    quoted_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings')
    status = models.CharField(max_length=16, choices=STATES, default='pending')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='kilasi_bookings_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-requested_date', '-pk']
        indexes = [models.Index(fields=['branch', 'status'])]

    def clean(self):
        super().clean()
        # Branch is legacy provenance only: this is one Transport department.

    def save(self, *args, **kwargs):
        creating = not self.pk
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f'KI-BK-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)

    @property
    def schedule_label(self):
        return (self.requested_date.strftime('%d %b %Y') +
                (' ' + self.requested_time.strftime('%H:%M') if self.requested_time else ' (time not recorded)'))

    @property
    def is_overdue(self):
        if self.status not in ('pending', 'confirmed'):
            return False
        if self.trip_id and self.trip.status == 'completed':
            return False
        today = timezone.localdate()
        if self.requested_date < today:
            return True
        if self.requested_date > today or not self.requested_time:
            return False
        return self.requested_time < timezone.localtime().time().replace(tzinfo=None)

    def __str__(self):
        return self.reference or f'Booking {self.pk}'


class TripPayment(models.Model):
    METHODS = [('cash', 'Cash'), ('mobile_money', 'Mobile money'), ('bank', 'Bank')]
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField(max_length=20, choices=METHODS, default='cash')
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='kilasi_trip_payments')
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='kilasi_trip_payments_voided')
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-paid_at', '-pk']
        indexes = [models.Index(fields=['trip', 'is_voided'])]

    def __str__(self):
        return f'{self.trip.reference} payment {self.amount}'


class TransportExpense(models.Model):
    CATEGORIES = [
        ('fuel', 'Fuel'), ('maintenance', 'Maintenance'), ('toll', 'Tolls'),
        ('parking', 'Parking'), ('driver', 'Driver allowance'), ('loading', 'Loading'),
        ('insurance', 'Insurance'), ('office', 'Office'), ('other', 'Other'),
    ]
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_transport_expenses', null=True, blank=True)
    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, null=True, blank=True, related_name='transport_expenses')
    category = models.CharField(max_length=20, choices=CATEGORIES, default='fuel')
    description = models.CharField(max_length=240)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    expense_date = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='kilasi_transport_expenses_created')
    created_at = models.DateTimeField(auto_now_add=True)
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='kilasi_transport_expenses_voided')
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-expense_date', '-pk']
        indexes = [models.Index(fields=['branch', 'expense_date'])]

    def clean(self):
        super().clean()
        # A trip may be linked to a department expense regardless of old Retail branch.

    def __str__(self):
        return f'{self.get_category_display()}: {self.amount}'


# KILASI_V2_PHASE4B_ACCESS
class TransportAccess(models.Model):
    """A strictly Transport-only identity, independent of Retail branches."""
    ROLES = [('manager', 'Transport Manager'), ('staff', 'Transport Staff'), ('driver', 'Transport Driver')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='transport_access')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='transport_accounts', null=True, blank=True)
    role = models.CharField(max_length=16, choices=ROLES, default='staff')
    driver = models.OneToOneField('kilasi_transport.Driver', on_delete=models.PROTECT, null=True, blank=True, related_name='portal_access')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} ({self.get_role_display()})'

# KILASI_V2_HAMA_STANDALONE_V1
