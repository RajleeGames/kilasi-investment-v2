from decimal import Decimal
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0')

class ShopSettings(models.Model):
    name = models.CharField(max_length=120, default='Mega Fish Point')
    phone = models.CharField(max_length=50, blank=True)
    logo = models.FileField(upload_to='branding/', blank=True, null=True)
    address = models.CharField(max_length=180, blank=True)
    receipt_footer = models.CharField(max_length=180, default='Asante kwa kununua Mega Fish Point.')
    currency = models.CharField(max_length=10, default='TZS')
    low_stock_default = models.DecimalField(max_digits=14, decimal_places=3, default=5)
    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
    def __str__(self): return self.name

class UserProfile(models.Model):
    ROLE_CHOICES=[('admin','Admin'),('cashier','Cashier')]
    user=models.OneToOneField(User,on_delete=models.CASCADE,related_name='profile')
    role=models.CharField(max_length=20,choices=ROLE_CHOICES,default='cashier')
    branch=models.ForeignKey('core.Branch', on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_profiles')
    def __str__(self): return f'{self.user.username} - {self.get_role_display()}'

class Category(models.Model):
    name=models.CharField(max_length=255,unique=True)
    is_active=models.BooleanField(default=True)
    class Meta: ordering=['name']; verbose_name_plural='Categories'
    def __str__(self): return self.name

class Branch(models.Model):
    name = models.CharField(max_length=120, unique=True)
    legacy_novman_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    contact = models.CharField(max_length=100, blank=True)
    legacy_novman_id = models.PositiveIntegerField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):


    # KILASI_V2_PHASE2_RETAIL — separate retail division from detailed Category.
    RETAIL_DIVISION_CHOICES = [
        ('furniture', 'Furniture'),
        ('electronics', 'Electronics'),
        ('magodoro', 'Magodoro / Mattresses'),
        ('other', 'Other / Unassigned'),
    ]
    retail_division = models.CharField(
        max_length=24, choices=RETAIL_DIVISION_CHOICES,
        default='other', db_index=True,
    )



    BASE_UNIT_CHOICES = [
     ('PCS', 'Piece'),
     ('BTL', 'Bottle'),
     ('CARTON', 'Carton'),
     ('KG', 'Kilogram'),
     ('G', 'Gram'),
     ('L', 'Litre'),
     ('ML', 'Millilitre'),
     ('M', 'Metre'),
     ('CM', 'Centimetre'),
     ('M2', 'Square Metre'),
        ]

    name = models.CharField(max_length=255, db_index=True)
    legacy_novman_id = models.PositiveIntegerField(unique=True, null=True, blank=True)
    barcode = models.CharField(max_length=50, unique=True, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    last_buying_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products'
    )

    base_unit = models.CharField(
        max_length=10,
        choices=BASE_UNIT_CHOICES
    )

    low_stock_level = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=5
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def stock_qty(self):
        return (
            self.batches.aggregate(
                v=Sum('quantity_remaining_base')
            )['v']
            or ZERO
        )

    @property
    def stock_value(self):
        return sum(
            (
                b.quantity_remaining_base * b.unit_cost_base
                for b in self.batches.all()
            ),
            ZERO
        )

    @property
    def is_low_stock(self):
        return self.stock_qty <= self.low_stock_level

class ProductUnit(models.Model):
    product=models.ForeignKey(Product,on_delete=models.CASCADE,related_name='selling_units')
    name=models.CharField(max_length=60)
    symbol=models.CharField(max_length=20)
    conversion_to_base=models.DecimalField(max_digits=14,decimal_places=3,validators=[MinValueValidator(Decimal('0.001'))])
    selling_price=models.DecimalField(max_digits=14,decimal_places=2,validators=[MinValueValidator(ZERO)])
    is_default=models.BooleanField(default=False)
    is_active=models.BooleanField(default=True)
    class Meta: unique_together=('product','name'); ordering=['product__name','conversion_to_base']
    def __str__(self): return f'{self.product.name} - {self.name}'

class Purchase(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='purchase_records')
    supplier_name=models.CharField(max_length=120,blank=True)
    invoice_number=models.CharField(max_length=80,blank=True)
    purchased_at=models.DateTimeField(default=timezone.now)
    notes=models.TextField(blank=True)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    @property
    def total(self): return self.items.aggregate(v=Sum('total_cost'))['v'] or ZERO
    def __str__(self): return f'PUR-{self.pk:05d}' if self.pk else 'Purchase'

class PurchaseItem(models.Model):
    purchase=models.ForeignKey(Purchase,on_delete=models.CASCADE,related_name='items')
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    unit=models.ForeignKey(ProductUnit,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=14,decimal_places=3)
    base_quantity=models.DecimalField(max_digits=14,decimal_places=3)
    cost_per_unit=models.DecimalField(max_digits=14,decimal_places=2)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2)
    unit_cost_base=models.DecimalField(max_digits=18,decimal_places=6)

class StockBatch(models.Model):
    SOURCE_CHOICES=[('OPENING','Opening Stock'),('PURCHASE','Purchase')]
    product=models.ForeignKey(Product,on_delete=models.PROTECT,related_name='batches')
    branch=models.ForeignKey(Branch,on_delete=models.PROTECT,null=True,blank=True,related_name='stock_batches')
    legacy_novman_id=models.PositiveIntegerField(unique=True,null=True,blank=True)
    legacy_selling_price=models.DecimalField(max_digits=14,decimal_places=2,null=True,blank=True)
    source=models.CharField(max_length=20,choices=SOURCE_CHOICES)
    purchase_item=models.OneToOneField(PurchaseItem,on_delete=models.CASCADE,null=True,blank=True,related_name='batch')
    reference=models.CharField(max_length=100,blank=True)
    quantity_received_base=models.DecimalField(max_digits=14,decimal_places=3)
    quantity_remaining_base=models.DecimalField(max_digits=14,decimal_places=3)
    unit_cost_base=models.DecimalField(max_digits=18,decimal_places=6)
    received_at=models.DateTimeField(default=timezone.now)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['received_at','id']
    def __str__(self): return f'{self.product.name} / {self.source} / {self.quantity_remaining_base}'


class Customer(models.Model):
    name=models.CharField(max_length=120)
    phone=models.CharField(max_length=40,blank=True,db_index=True)
    email=models.EmailField(blank=True)
    notes=models.TextField(blank=True)
    is_active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=['name','id']
    def __str__(self): return self.name
    @property
    def credit_sales_total(self):
        return self.credit_sales.filter(status='completed',payment_method='debt').aggregate(v=Sum('grand_total'))['v'] or ZERO
    @property
    def payments_total(self):
        return self.payments.filter(is_voided=False).aggregate(v=Sum('amount'))['v'] or ZERO
    @property
    def current_balance(self):
        balance=self.credit_sales_total-self.payments_total
        return balance if balance>ZERO else ZERO
    @property
    def is_debtor(self): return self.current_balance>ZERO

class Sale(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='sale_records')
    STATUS_CHOICES=[('completed','Completed'),('voided','Voided')]
    PAYMENT_CHOICES=[('cash','Cash'),('mobile_money','Mobile Money'),('bank','Bank'),('debt','Debt')]
    receipt_no=models.CharField(max_length=30,unique=True,blank=True)
    payment_method=models.CharField(max_length=30,choices=PAYMENT_CHOICES,default='cash')
    customer=models.ForeignKey(Customer,on_delete=models.PROTECT,null=True,blank=True,related_name='credit_sales')
    customer_name=models.CharField(max_length=120,blank=True)
    subtotal=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    item_discount_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    sale_discount=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    grand_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    cogs_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    profit_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    status=models.CharField(max_length=20,choices=STATUS_CHOICES,default='completed')
    sold_at=models.DateTimeField(default=timezone.now)
    cashier=models.ForeignKey(User,on_delete=models.PROTECT,related_name='sales')
    voided_at=models.DateTimeField(null=True,blank=True)
    voided_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='voided_sales')
    void_reason=models.CharField(max_length=180,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-sold_at','-id']
    def save(self,*args,**kwargs):
        super().save(*args,**kwargs)
        if not self.receipt_no:
            self.receipt_no=f'NOV-{self.pk:06d}'
            super().save(update_fields=['receipt_no'])
    def __str__(self): return self.receipt_no or f'Sale {self.pk}'


class CustomerPayment(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='customerpayment_records')
    METHOD_CHOICES=[('cash','Cash'),('mobile_money','Mobile Money'),('bank','Bank')]
    customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name='payments')
    amount=models.DecimalField(max_digits=14,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    method=models.CharField(max_length=30,choices=METHOD_CHOICES,default='cash')
    reference=models.CharField(max_length=100,blank=True)
    notes=models.CharField(max_length=180,blank=True)
    paid_at=models.DateTimeField(default=timezone.now)
    received_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='customer_payments_received')
    is_voided=models.BooleanField(default=False)
    voided_at=models.DateTimeField(null=True,blank=True)
    voided_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='voided_customer_payments')
    void_reason=models.CharField(max_length=180,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-paid_at','-id']
    def __str__(self): return f'{self.customer.name} - {self.amount}'

class SaleItem(models.Model):
    sale=models.ForeignKey(Sale,on_delete=models.CASCADE,related_name='items')
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    unit=models.ForeignKey(ProductUnit,on_delete=models.PROTECT)
    quantity=models.DecimalField(max_digits=14,decimal_places=3)
    conversion_to_base=models.DecimalField(max_digits=14,decimal_places=3)
    base_quantity=models.DecimalField(max_digits=14,decimal_places=3)
    normal_unit_price=models.DecimalField(max_digits=14,decimal_places=2)
    line_subtotal=models.DecimalField(max_digits=14,decimal_places=2)
    discount_amount=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    final_total=models.DecimalField(max_digits=14,decimal_places=2)
    cost_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    profit_before_sale_discount=models.DecimalField(max_digits=14,decimal_places=2,default=0)

class SaleAllocation(models.Model):
    sale_item=models.ForeignKey(SaleItem,on_delete=models.CASCADE,related_name='allocations')
    batch=models.ForeignKey(StockBatch,on_delete=models.PROTECT,related_name='sale_allocations')
    quantity_base=models.DecimalField(max_digits=14,decimal_places=3)
    unit_cost_base=models.DecimalField(max_digits=18,decimal_places=6)
    cost_total=models.DecimalField(max_digits=14,decimal_places=2)

class StockAdjustment(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='stockadjustment_records')
    TYPE_CHOICES=[('increase','Increase'),('decrease','Decrease')]
    product=models.ForeignKey(Product,on_delete=models.PROTECT,related_name='adjustments')
    adjustment_type=models.CharField(max_length=20,choices=TYPE_CHOICES)
    quantity_base=models.DecimalField(max_digits=14,decimal_places=3)
    reason=models.CharField(max_length=180)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class Expense(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='expense_records')

    CATEGORY_CHOICES = [
        ('electricity', 'Electricity'),
        ('water', 'Water'),
        ('Food','Food'),
        ('transport', 'Transport'),
        ('fuel', 'Fuel'),
        ('rent', 'Rent'),
        ('salary', 'Salary / Wages'),
        ('loading', 'Loading & Offloading'),
        ('delivery', 'Customer Delivery'),
        ('maintenance', 'Repairs & Maintenance'),
        ('office', 'Office Expenses'),
        ('internet', 'Internet & Communication'),
        ('cleaning', 'Cleaning'),
        ('security', 'Security'),
        ('bank_charges', 'Bank / Mobile Money Charges'),
        ('licenses', 'Licenses & Permits'),
        ('marketing', 'Marketing / Advertising'),
        ('stationery', 'Stationery'),
        ('packaging', 'Packaging'),
        ('other', 'Other'),
    ]

    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        default='other'
    )

    description = models.CharField(max_length=180)

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    expense_date = models.DateField(default=timezone.localdate)

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-expense_date', '-id']

    def __str__(self):
        return f"{self.get_category_display()} - {self.amount}"


class CashClosing(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='cashclosing_records')
    cashier=models.ForeignKey(User,on_delete=models.PROTECT,related_name='cash_closings')
    close_date=models.DateField(default=timezone.localdate)
    opening_float=models.DecimalField(max_digits=14,decimal_places=2,default=0,validators=[MinValueValidator(ZERO)])
    cash_sales=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    mobile_money_sales=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    bank_sales=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    debt_sales=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    debt_cash_collected=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    debt_mobile_collected=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    debt_bank_collected=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    sale_count=models.PositiveIntegerField(default=0)
    debt_payment_count=models.PositiveIntegerField(default=0)
    cash_paid_out=models.DecimalField(max_digits=14,decimal_places=2,default=0,validators=[MinValueValidator(ZERO)])
    expected_cash=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    counted_cash=models.DecimalField(max_digits=14,decimal_places=2,default=0,validators=[MinValueValidator(ZERO)])
    difference=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    notes=models.CharField(max_length=240,blank=True)
    closed_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='cash_closings_recorded')
    closed_at=models.DateTimeField(auto_now_add=True)
    amended_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='cash_closings_amended')
    amended_at=models.DateTimeField(null=True,blank=True)
    amendment_reason=models.CharField(max_length=240,blank=True)
    class Meta:
        ordering=['-close_date','-closed_at','-id']
        constraints=[models.UniqueConstraint(fields=['cashier','close_date'],name='unique_cashier_daily_closing')]
    @property
    def reference(self): return f'CLOSE-{self.pk:05d}' if self.pk else 'Closing'
    @property
    def sales_total(self): return self.cash_sales+self.mobile_money_sales+self.bank_sales+self.debt_sales
    @property
    def debt_collected_total(self): return self.debt_cash_collected+self.debt_mobile_collected+self.debt_bank_collected
    def __str__(self): return f'{self.cashier.username} - {self.close_date}'


class Stocktake(models.Model):
    branch=models.ForeignKey('core.Branch',on_delete=models.PROTECT,null=True,blank=True,related_name='stocktake_records')
    STATUS_CHOICES=[('draft','Draft'),('posted','Posted'),('cancelled','Cancelled')]
    count_date=models.DateField(default=timezone.localdate)
    status=models.CharField(max_length=20,choices=STATUS_CHOICES,default='draft')
    notes=models.CharField(max_length=240,blank=True)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='stocktakes_created')
    created_at=models.DateTimeField(auto_now_add=True)
    posted_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='stocktakes_posted')
    posted_at=models.DateTimeField(null=True,blank=True)
    cancelled_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='stocktakes_cancelled')
    cancelled_at=models.DateTimeField(null=True,blank=True)
    class Meta: ordering=['-count_date','-created_at','-id']
    @property
    def reference(self): return f'STK-{self.pk:05d}' if self.pk else 'Stocktake'
    @property
    def total_variance_lines(self): return self.lines.exclude(difference_base=0).count()
    def __str__(self): return self.reference


class StocktakeLine(models.Model):
    stocktake=models.ForeignKey(Stocktake,on_delete=models.CASCADE,related_name='lines')
    product=models.ForeignKey(Product,on_delete=models.PROTECT,related_name='stocktake_lines')
    system_qty_base=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    counted_input_qty=models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    count_unit=models.ForeignKey(ProductUnit,on_delete=models.SET_NULL,null=True,blank=True,related_name='stocktake_lines')
    count_unit_name=models.CharField(max_length=60,blank=True)
    count_unit_symbol=models.CharField(max_length=20,blank=True)
    count_conversion=models.DecimalField(max_digits=14,decimal_places=3,default=1)
    counted_qty_base=models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    difference_base=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    unit_cost_base=models.DecimalField(max_digits=18,decimal_places=6,default=0)
    adjustment=models.OneToOneField(StockAdjustment,on_delete=models.SET_NULL,null=True,blank=True,related_name='stocktake_line')
    class Meta:
        ordering=['product__name','id']
        constraints=[models.UniqueConstraint(fields=['stocktake','product'],name='unique_product_per_stocktake')]
    def __str__(self): return f'{self.stocktake.reference} - {self.product.name}'



class PersonalReceivable(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40, blank=True)

    amount_owed = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    owed_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)

    notes = models.CharField(max_length=240, blank=True)

    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='personal_receivables_created'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-owed_date', '-id']

    @property
    def reference(self):
        return f'OWE-{self.pk:05d}' if self.pk else 'Receivable'

    @property
    def payments_total(self):
        return (
            self.payments.aggregate(v=Sum('amount'))['v']
            or ZERO
        )

    @property
    def balance(self):
        remaining = self.amount_owed - self.payments_total
        return remaining if remaining > ZERO else ZERO

    @property
    def is_paid(self):
        return self.balance <= ZERO

    def __str__(self):
        return f'{self.name} - {self.amount_owed}'


class PersonalReceivablePayment(models.Model):
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('mobile_money', 'Mobile Money'),
        ('bank', 'Bank'),
    ]

    receivable = models.ForeignKey(
        PersonalReceivable,
        on_delete=models.CASCADE,
        related_name='payments'
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )

    method = models.CharField(
        max_length=30,
        choices=METHOD_CHOICES,
        default='cash'
    )

    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=180, blank=True)

    paid_at = models.DateTimeField(default=timezone.now)

    received_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='personal_receivable_payments_received'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at', '-id']

    def __str__(self):
        return f'{self.receivable.name} - {self.amount}'

class StockTransfer(models.Model):
    source_branch=models.ForeignKey(Branch,on_delete=models.PROTECT,related_name='outgoing_transfers')
    destination_branch=models.ForeignKey(Branch,on_delete=models.PROTECT,related_name='incoming_transfers')
    product=models.ForeignKey(Product,on_delete=models.PROTECT,related_name='stock_transfers')
    quantity_base=models.DecimalField(max_digits=14,decimal_places=3)
    notes=models.CharField(max_length=240,blank=True)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-created_at','-id']
    def __str__(self): return f'TRF-{self.pk:05d}'


class StockTransferLine(models.Model):
    transfer=models.ForeignKey(StockTransfer,on_delete=models.PROTECT,related_name='lines')
    source_batch=models.ForeignKey(StockBatch,on_delete=models.PROTECT,related_name='transfer_consumptions')
    destination_batch=models.ForeignKey(StockBatch,on_delete=models.PROTECT,related_name='transfer_receipts')
    quantity_base=models.DecimalField(max_digits=14,decimal_places=3)
    unit_cost_base=models.DecimalField(max_digits=18,decimal_places=6)
