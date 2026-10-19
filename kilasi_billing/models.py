"""Document records. No mutation of Novman stock or historical sale prices."""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0.00')


def cash(value):
    return Decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class Proforma(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('issued', 'Issued'), ('cancelled', 'Cancelled')]
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_proformas')
    number = models.CharField(max_length=30, unique=True, blank=True)
    customer_name = models.CharField(max_length=160)
    customer_phone = models.CharField(max_length=50, blank=True)
    customer_address = models.CharField(max_length=250, blank=True)
    issue_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def save(self, *args, **kwargs):
        is_new = not self.pk
        super().save(*args, **kwargs)
        if is_new and not self.number:
            self.number = f'KI-PF-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(number=self.number)

    @property
    def total(self):
        return sum((line.total for line in self.lines.all()), ZERO)

    def __str__(self):
        return self.number or f'Proforma {self.pk}'


class ProformaLine(models.Model):
    proforma = models.ForeignKey(Proforma, on_delete=models.CASCADE, related_name='lines')
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    unit = models.CharField(max_length=32, default='PCS')
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)])
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'pk']

    @property
    def subtotal(self):
        return cash(self.quantity * self.unit_price)

    @property
    def total(self):
        return cash(self.subtotal - self.discount)

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.quantity is not None and self.unit_price is not None and self.discount is not None:
            if self.discount > cash(self.quantity * self.unit_price):
                raise ValidationError({'discount': 'Discount cannot exceed the line amount.'})


class Invoice(models.Model):
    """One immutable invoice identity per Novman sale, no secondary revenue event."""
    sale = models.OneToOneField('core.Sale', on_delete=models.PROTECT, related_name='kilasi_invoice')
    number = models.CharField(max_length=30, unique=True, blank=True)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def save(self, *args, **kwargs):
        is_new = not self.pk
        super().save(*args, **kwargs)
        if is_new and not self.number:
            self.number = f'KI-INV-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(number=self.number)

    @property
    def paid(self):
        if self.sale.payment_method != 'debt':
            return cash(self.sale.grand_total)
        return cash(self.allocations.filter(payment__is_voided=False).aggregate(value=Sum('payment__amount'))['value'] or ZERO)

    @property
    def balance(self):
        return max(ZERO, cash(self.sale.grand_total) - self.paid)

    @property
    def payment_state(self):
        if self.sale.status != 'completed':
            return 'Voided'
        if self.sale.payment_method != 'debt' or self.balance == ZERO:
            return 'Paid'
        if self.paid > ZERO:
            return 'Part paid'
        return 'Unpaid'

    def __str__(self):
        return self.number or f'Invoice {self.pk}'


class InvoiceAllocation(models.Model):
    """A single existing CustomerPayment allocated to a single invoice.

    Recording through billing creates exactly one core CustomerPayment, then
    links it here. Voiding core payment reverses this allocation automatically.
    """
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='allocations')
    payment = models.OneToOneField('core.CustomerPayment', on_delete=models.PROTECT, related_name='kilasi_invoice_allocation')
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-allocated_at', '-pk']


class DeliveryNote(models.Model):
    """Full-sale delivery document; doesn't represent another stock movement."""
    sale = models.OneToOneField('core.Sale', on_delete=models.PROTECT, related_name='kilasi_delivery_note')
    number = models.CharField(max_length=30, unique=True, blank=True)
    delivered_to = models.CharField(max_length=160)
    delivery_address = models.CharField(max_length=250, blank=True)
    vehicle_or_driver = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    issue_date = models.DateField(default=timezone.localdate)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def save(self, *args, **kwargs):
        is_new = not self.pk
        super().save(*args, **kwargs)
        if is_new and not self.number:
            self.number = f'KI-DN-{self.pk:06d}'
            type(self).objects.filter(pk=self.pk).update(number=self.number)

    def __str__(self):
        return self.number or f'Delivery note {self.pk}'
