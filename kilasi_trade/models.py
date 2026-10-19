"""New trade records. Legacy IDs are mapping keys, not destination primary keys.

Never create duplicate Novman Sale entries for a Genji or a Top-Up, and never
import old stock reductions as new live transactions.
"""
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

ZERO = Decimal('0.00')


class GenjiSale(models.Model):
    STATUS = [('completed', 'Completed'), ('voided', 'Voided')]
    legacy_kilasi_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_genji_sales')
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=50, blank=True)
    item_name = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    buy_date = models.DateField()
    sell_date = models.DateField()
    buying_cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    selling_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=12, choices=STATUS, default='completed', db_index=True)
    void_reason = models.CharField(max_length=240, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='kilasi_genji_created')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sell_date', '-pk']

    @property
    def total_buying(self):
        return self.buying_cost * self.quantity

    @property
    def total_selling(self):
        return self.selling_price * self.quantity

    @property
    def profit(self):
        return self.total_selling - self.total_buying

    def __str__(self):
        return f'Genji #{self.pk}: {self.item_name}'


CATEGORY = [('electronics', 'Electronics'), ('furniture', 'Furniture'), ('magodoro', 'Magodoro')]


class TopupTransaction(models.Model):
    """Posted exchange. Financial consideration = used value + cash difference.

    The exchange's cash component is NOT the whole selling price. Outgoing
    batches live in TopupConsumption; used inventory is held separately.
    """
    legacy_kilasi_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_topups')
    customer = models.ForeignKey('core.Customer', null=True, blank=True, on_delete=models.SET_NULL, related_name='kilasi_topups')
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=50, blank=True)
    new_product = models.ForeignKey('core.Product', on_delete=models.PROTECT, related_name='kilasi_topups_out')
    new_product_qty = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    used_item_name = models.CharField(max_length=255)
    used_item_category = models.CharField(max_length=20, choices=CATEGORY, blank=True)
    used_item_condition = models.CharField(max_length=255, blank=True)
    used_item_buying_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    addon_amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    outgoing_cogs = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=[('completed','Completed'),('cancelled','Cancelled')], default='completed')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='kilasi_topups_created')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def total_consideration(self):
        return self.used_item_buying_price + self.addon_amount

    @property
    def exchange_profit(self):
        return self.total_consideration - self.outgoing_cogs

    def __str__(self):
        return f'Top-up #{self.pk}'


class TopupConsumption(models.Model):
    """Immutable cost trace of each FIFO stock batch consumed in an exchange."""
    transaction = models.ForeignKey(TopupTransaction, on_delete=models.PROTECT, related_name='fifo_allocations')
    batch = models.ForeignKey('core.StockBatch', on_delete=models.PROTECT, related_name='kilasi_topup_allocations')
    quantity_base = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost_base = models.DecimalField(max_digits=18, decimal_places=6)
    cost_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['transaction', 'batch'], name='kilasi_topup_batch_unique')]


class UsedTopupStock(models.Model):
    legacy_kilasi_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    transaction = models.OneToOneField(TopupTransaction, on_delete=models.PROTECT, related_name='used_stock')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_used_stock')
    item_name = models.CharField(max_length=255)
    item_category = models.CharField(max_length=20, choices=CATEGORY, blank=True)
    condition = models.CharField(max_length=255, blank=True)
    buying_price = models.DecimalField(max_digits=14, decimal_places=2)
    asking_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=[('available','Available'),('sold','Sold')], default='available')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return self.item_name


class UsedTopupSale(models.Model):
    legacy_kilasi_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    used_stock = models.OneToOneField(UsedTopupStock, on_delete=models.PROTECT, related_name='sale')
    customer = models.ForeignKey('core.Customer', null=True, blank=True, on_delete=models.SET_NULL)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=50, blank=True)
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_used_sales')
    sold_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)])
    note = models.TextField(blank=True)
    sold_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def profit(self):
        return self.sold_price - self.used_stock.buying_price

    def __str__(self):
        return f'Used-item sale #{self.pk}'
