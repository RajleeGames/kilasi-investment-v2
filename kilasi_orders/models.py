"""Retail customer orders, deposits and auditable, branch-aware sale returns.

Keep order deposits outside Novman POS revenue. Posted sale returns are
separate credit/refund records; Novman Sale totals are historic snapshots.
Legacy IDs map data for imports but are never target PKs.
"""
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0.00')

class CustomerOrder(models.Model):
    STATUS = [('pending','Pending'),('in_progress','In progress'),('completed','Completed'),('cancelled','Cancelled')]
    legacy_kilasi_id = models.PositiveIntegerField(null=True,blank=True,unique=True)
    branch = models.ForeignKey('core.Branch',on_delete=models.PROTECT,related_name='kilasi_customer_orders')
    customer = models.ForeignKey('core.Customer',on_delete=models.SET_NULL,null=True,blank=True,related_name='kilasi_customer_orders')
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=30,blank=True)
    product = models.ForeignKey('core.Product',null=True,blank=True,on_delete=models.PROTECT,related_name='kilasi_customer_orders')
    custom_product_name = models.CharField(max_length=150,blank=True)
    description = models.TextField()
    quantity = models.PositiveIntegerField(default=1,validators=[MinValueValidator(1)])
    total_amount = models.DecimalField(max_digits=14,decimal_places=2,validators=[MinValueValidator(ZERO)])
    order_status = models.CharField(max_length=20,choices=STATUS,default='pending')
    expected_date = models.DateField(null=True,blank=True)
    completed_at = models.DateTimeField(null=True,blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='kilasi_customer_orders_created')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-created_at','-pk']
    @property
    def order_no(self):
        return f'KI-ORD-{self.pk:06d}' if self.pk else 'NEW ORDER'
    @property
    def product_name(self):
        return self.product.name if self.product_id else (self.custom_product_name or 'Custom order')
    @property
    def paid_amount(self):
        if not self.pk:return ZERO
        return self.payments.filter(is_voided=False).aggregate(v=Sum('amount'))['v'] or ZERO
    @property
    def balance(self):
        return max(ZERO,self.total_amount-self.paid_amount)
    @property
    def payment_status(self):
        paid=self.paid_amount
        return 'Paid' if paid>=self.total_amount and self.total_amount>0 else 'Partial' if paid>0 else 'Unpaid'
    def __str__(self):return f'{self.order_no}: {self.customer_name}'

class OrderPayment(models.Model):
    METHODS = [('cash','Cash'),('mobile_money','Mobile Money'),('bank','Bank')]
    legacy_kilasi_id = models.PositiveIntegerField(null=True,blank=True,unique=True)
    order = models.ForeignKey(CustomerOrder,on_delete=models.PROTECT,related_name='payments')
    amount = models.DecimalField(max_digits=14,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField(max_length=20,choices=METHODS)
    reference = models.CharField(max_length=100,blank=True)
    notes = models.CharField(max_length=240,blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='kilasi_order_payments_received')
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True,blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='kilasi_order_payments_voided')
    void_reason = models.CharField(max_length=240,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-paid_at','-pk']
    def __str__(self):return f'{self.order.order_no}: {self.amount}'

class SalesReturn(models.Model):
    """Posted credit/refund record for one historical completed, non-credit POS sale."""
    legacy_kilasi_id = models.PositiveIntegerField(null=True,blank=True,unique=True)
    branch = models.ForeignKey('core.Branch',on_delete=models.PROTECT,related_name='kilasi_sales_returns')
    sale = models.ForeignKey('core.Sale',on_delete=models.PROTECT,related_name='kilasi_returns')
    reason = models.CharField(max_length=240)
    refund_method = models.CharField(max_length=20,choices=OrderPayment.METHODS)
    refund_reference = models.CharField(max_length=100,blank=True)
    total_refund = models.DecimalField(max_digits=14,decimal_places=2,default=ZERO)
    stock_cost_restored = models.DecimalField(max_digits=14,decimal_places=2,default=ZERO)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='kilasi_returns_posted')
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at','-pk']
    @property
    def return_no(self):return f'KI-RET-{self.pk:06d}' if self.pk else 'NEW RETURN'
    def __str__(self):return self.return_no

class SalesReturnLine(models.Model):
    legacy_kilasi_id = models.PositiveIntegerField(null=True,blank=True,unique=True)
    sale_return = models.ForeignKey(SalesReturn,on_delete=models.PROTECT,related_name='lines')
    sale_item = models.ForeignKey('core.SaleItem',on_delete=models.PROTECT,related_name='kilasi_return_lines')
    quantity = models.DecimalField(max_digits=14,decimal_places=3)
    base_quantity = models.DecimalField(max_digits=14,decimal_places=3)
    refund_amount = models.DecimalField(max_digits=14,decimal_places=2)
    cost_restored = models.DecimalField(max_digits=14,decimal_places=2,default=ZERO)
    restocked = models.BooleanField(default=False)
    def __str__(self):return f'{self.sale_item.product.name} x {self.quantity}'

class ReturnStockAllocation(models.Model):
    """Trace every return to original FIFO allocation, including damaged items.

    If item is not resaleable, new_stock_batch=None, preventing a later
    return from reusing this same source allocation cost or stock quantity.
    """
    return_line = models.ForeignKey(SalesReturnLine,on_delete=models.PROTECT,related_name='restored_allocations')
    original_allocation = models.ForeignKey('core.SaleAllocation',on_delete=models.PROTECT,related_name='kilasi_return_restorations')
    new_stock_batch = models.OneToOneField('core.StockBatch',on_delete=models.PROTECT,null=True,blank=True,related_name='kilasi_return_restoration')
    quantity_base = models.DecimalField(max_digits=14,decimal_places=3)
    cost_total = models.DecimalField(max_digits=14,decimal_places=2)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['return_line','original_allocation'],name='kilasi_return_source_once')]
