from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from .models import (
    Sale, SaleItem, SaleAllocation, StockBatch, StockAdjustment, Purchase, PurchaseItem,
    Customer, CustomerPayment, CashClosing, Stocktake, StocktakeLine, Product, ProductUnit,
)

ZERO=Decimal('0')
TWOPLACES=Decimal('0.01')

class StockError(Exception): pass

def D(value, default='0'):
    try: return Decimal(str(value if value not in (None,'') else default).replace(',',''))
    except (InvalidOperation, TypeError, ValueError): return Decimal(default)

def money(v): return D(v).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

@transaction.atomic
def add_opening_stock(*, product, quantity_base, unit_cost_base, reference, user):
    qty=D(quantity_base)
    cost=D(unit_cost_base)
    if qty <= 0: raise StockError('Opening quantity must be greater than zero.')
    if cost < 0: raise StockError('Opening cost cannot be negative.')
    return StockBatch.objects.create(product=product,source='OPENING',reference=reference or 'Opening stock',
        quantity_received_base=qty,quantity_remaining_base=qty,unit_cost_base=cost,received_at=timezone.now(),created_by=user)

@transaction.atomic
def add_purchase(*, product, unit, quantity, cost_per_unit, supplier_name, invoice_number, notes, user):
    qty=D(quantity); cpu=D(cost_per_unit)
    if qty <= 0 or cpu < 0: raise StockError('Enter a valid purchase quantity and cost.')
    if unit.product_id != product.id: raise StockError('Selected unit does not belong to this product.')
    base_qty=qty*unit.conversion_to_base
    total=money(qty*cpu)
    unit_cost_base=(total/base_qty) if base_qty else ZERO
    purchase=Purchase.objects.create(supplier_name=supplier_name,invoice_number=invoice_number,notes=notes,created_by=user)
    item=PurchaseItem.objects.create(purchase=purchase,product=product,unit=unit,quantity=qty,base_quantity=base_qty,
        cost_per_unit=cpu,total_cost=total,unit_cost_base=unit_cost_base)
    StockBatch.objects.create(product=product,source='PURCHASE',purchase_item=item,reference=invoice_number or f'PUR-{purchase.id:05d}',
        quantity_received_base=base_qty,quantity_remaining_base=base_qty,unit_cost_base=unit_cost_base,received_at=purchase.purchased_at,created_by=user)
    return purchase

@transaction.atomic
def create_sale(*, cart, payment_method, customer_name, sale_discount, cashier, customer_phone=''):
    if not cart: raise StockError('Cart is empty.')
    sale_discount=money(sale_discount)
    if payment_method not in dict(Sale.PAYMENT_CHOICES):
        raise StockError('Choose a valid payment method.')

    customer_name=(customer_name or '').strip()
    customer_phone=(customer_phone or '').strip()
    customer=None

    if payment_method=='debt':
        if not customer_name:
            raise StockError('Customer name is required for a debt sale.')

        if customer_phone:
            customer=Customer.objects.filter(phone__iexact=customer_phone,is_active=True).order_by('id').first()

        if customer is None:
            customer=Customer.objects.filter(name__iexact=customer_name,is_active=True).order_by('id').first()

        if customer is None:
            customer=Customer.objects.create(name=customer_name,phone=customer_phone)
        else:
            changed=[]
            if customer.name!=customer_name:
                customer.name=customer_name
                changed.append('name')
            if customer_phone and customer.phone!=customer_phone:
                customer.phone=customer_phone
                changed.append('phone')
            if changed:
                customer.save(update_fields=changed+['updated_at'])

    sale=Sale.objects.create(
        payment_method=payment_method,
        customer=customer,
        customer_name=customer_name,
        sale_discount=sale_discount,
        cashier=cashier,
    )
    subtotal=ZERO; item_discounts=ZERO; cogs=ZERO
    from .models import Product, ProductUnit
    for row in cart:
        product=Product.objects.select_for_update().get(pk=int(row['product_id']),is_active=True)
        unit=ProductUnit.objects.get(pk=int(row['unit_id']),product=product,is_active=True)
        qty=D(row.get('quantity'))
        discount=money(row.get('discount',0))
        if qty <= 0: raise StockError(f'Quantity for {product.name} must be greater than zero.')
        line_subtotal=money(qty*unit.selling_price)
        if discount < 0 or discount > line_subtotal: raise StockError(f'Invalid discount for {product.name}.')
        final=money(line_subtotal-discount)
        base_qty=qty*unit.conversion_to_base
        available=product.batches.aggregate(v=Sum('quantity_remaining_base'))['v'] or ZERO
        if available < base_qty: raise StockError(f'Not enough {product.name}. Available: {available} {product.base_unit}.')
        item=SaleItem.objects.create(sale=sale,product=product,unit=unit,quantity=qty,conversion_to_base=unit.conversion_to_base,
            base_quantity=base_qty,normal_unit_price=unit.selling_price,line_subtotal=line_subtotal,discount_amount=discount,final_total=final)
        remaining=base_qty; item_cost=ZERO
        batches=StockBatch.objects.select_for_update().filter(product=product,quantity_remaining_base__gt=0).order_by('received_at','id')
        for batch in batches:
            if remaining <= 0: break
            take=min(remaining,batch.quantity_remaining_base)
            allocation_cost=money(take*batch.unit_cost_base)
            SaleAllocation.objects.create(sale_item=item,batch=batch,quantity_base=take,unit_cost_base=batch.unit_cost_base,cost_total=allocation_cost)
            batch.quantity_remaining_base=F('quantity_remaining_base')-take
            batch.save(update_fields=['quantity_remaining_base'])
            batch.refresh_from_db(fields=['quantity_remaining_base'])
            item_cost += allocation_cost
            remaining -= take
        if remaining > 0: raise StockError(f'Could not allocate stock for {product.name}.')
        item.cost_total=money(item_cost)
        item.profit_before_sale_discount=money(final-item.cost_total)
        item.save(update_fields=['cost_total','profit_before_sale_discount'])
        subtotal += line_subtotal; item_discounts += discount; cogs += item.cost_total
    net_before_sale_discount=money(subtotal-item_discounts)
    if sale_discount < 0 or sale_discount > net_before_sale_discount: raise StockError('Sale discount cannot exceed the bill amount.')
    grand=money(net_before_sale_discount-sale_discount)
    sale.subtotal=money(subtotal); sale.item_discount_total=money(item_discounts); sale.grand_total=grand; sale.cogs_total=money(cogs); sale.profit_total=money(grand-cogs)
    sale.save(update_fields=['subtotal','item_discount_total','grand_total','cogs_total','profit_total'])
    return sale

@transaction.atomic
def void_sale(*, sale, user, reason):
    sale=Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.status=='voided': raise StockError('Sale is already voided.')
    for item in sale.items.prefetch_related('allocations'):
        for allocation in item.allocations.all():
            StockBatch.objects.filter(pk=allocation.batch_id).update(quantity_remaining_base=F('quantity_remaining_base')+allocation.quantity_base)
    sale.status='voided'; sale.voided_at=timezone.now(); sale.voided_by=user; sale.void_reason=reason or 'Voided by admin'; sale.save()
    return sale

@transaction.atomic
def decrease_stock_fifo(*, product, quantity, user, reason):
    qty=D(quantity)
    if qty<=0: raise StockError('Quantity must be greater than zero.')
    available=product.stock_qty
    if available<qty: raise StockError(f'Not enough stock. Available {available} {product.base_unit}.')
    remaining=qty
    for batch in StockBatch.objects.select_for_update().filter(product=product,quantity_remaining_base__gt=0).order_by('received_at','id'):
        if remaining<=0: break
        take=min(remaining,batch.quantity_remaining_base)
        batch.quantity_remaining_base=F('quantity_remaining_base')-take; batch.save(update_fields=['quantity_remaining_base'])
        remaining-=take
    return StockAdjustment.objects.create(product=product,adjustment_type='decrease',quantity_base=qty,reason=reason,created_by=user)

@transaction.atomic
def increase_stock(*, product, quantity, unit_cost_base, user, reason):
    qty=D(quantity); cost=D(unit_cost_base)
    if qty<=0: raise StockError('Quantity must be greater than zero.')
    StockBatch.objects.create(product=product,source='OPENING',reference=f'Adjustment: {reason}',quantity_received_base=qty,
        quantity_remaining_base=qty,unit_cost_base=cost,received_at=timezone.now(),created_by=user)
    return StockAdjustment.objects.create(product=product,adjustment_type='increase',quantity_base=qty,reason=reason,created_by=user)


@transaction.atomic
def record_customer_payment(*, customer, amount, method, reference, notes, user):
    customer=Customer.objects.select_for_update().get(pk=customer.pk)
    amount=money(amount)
    if amount<=0:
        raise StockError('Payment amount must be greater than zero.')
    if method not in dict(CustomerPayment.METHOD_CHOICES):
        raise StockError('Choose a valid payment method.')

    balance=customer.current_balance
    if balance<=0:
        raise StockError('This customer has no outstanding debt.')
    if amount>balance:
        raise StockError(f'Payment cannot exceed the outstanding balance of {money(balance)} TZS.')

    return CustomerPayment.objects.create(
        customer=customer,
        amount=amount,
        method=method,
        reference=(reference or '').strip(),
        notes=(notes or '').strip(),
        received_by=user,
    )


@transaction.atomic
def void_customer_payment(*, payment, user, reason):
    payment=CustomerPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_voided:
        raise StockError('This payment is already voided.')
    payment.is_voided=True
    payment.voided_at=timezone.now()
    payment.voided_by=user
    payment.void_reason=(reason or 'Voided by admin').strip()
    payment.save(update_fields=['is_voided','voided_at','voided_by','void_reason'])
    return payment


def _sum_sale_method(qs, method):
    return money(qs.filter(payment_method=method).aggregate(v=Sum('grand_total'))['v'] or ZERO)


def _sum_customer_payment_method(qs, method):
    return money(qs.filter(method=method).aggregate(v=Sum('amount'))['v'] or ZERO)


def calculate_cash_closing_snapshot(*, cashier, close_date):
    """Return the live sales/collection totals used by End Day."""
    sales=Sale.objects.filter(status='completed',cashier=cashier,sold_at__date=close_date)
    payments=CustomerPayment.objects.filter(is_voided=False,received_by=cashier,paid_at__date=close_date)
    return {
        'cash_sales':_sum_sale_method(sales,'cash'),
        'mobile_money_sales':_sum_sale_method(sales,'mobile_money'),
        'bank_sales':_sum_sale_method(sales,'bank'),
        'debt_sales':_sum_sale_method(sales,'debt'),
        'debt_cash_collected':_sum_customer_payment_method(payments,'cash'),
        'debt_mobile_collected':_sum_customer_payment_method(payments,'mobile_money'),
        'debt_bank_collected':_sum_customer_payment_method(payments,'bank'),
        'sale_count':sales.count(),
        'payment_count':payments.count(),
    }


@transaction.atomic
def close_cash_day(*, cashier, close_date, opening_float, cash_paid_out, counted_cash, notes, user):
    if close_date>timezone.localdate():
        raise StockError('You cannot close a future date.')
    if CashClosing.objects.select_for_update().filter(cashier=cashier,close_date=close_date).exists():
        raise StockError('This cashier already has a closing for that date.')

    opening_float=money(opening_float)
    cash_paid_out=money(cash_paid_out)
    counted_cash=money(counted_cash)
    if opening_float<0 or cash_paid_out<0 or counted_cash<0:
        raise StockError('Opening float, cash paid out and counted cash cannot be negative.')

    snap=calculate_cash_closing_snapshot(cashier=cashier,close_date=close_date)
    available_cash=money(opening_float+snap['cash_sales']+snap['debt_cash_collected'])
    if cash_paid_out>available_cash:
        raise StockError(f'Cash paid out cannot exceed available cash of {available_cash} TZS.')
    expected=money(available_cash-cash_paid_out)
    difference=money(counted_cash-expected)

    return CashClosing.objects.create(
        cashier=cashier,
        close_date=close_date,
        opening_float=opening_float,
        cash_sales=snap['cash_sales'],
        mobile_money_sales=snap['mobile_money_sales'],
        bank_sales=snap['bank_sales'],
        debt_sales=snap['debt_sales'],
        debt_cash_collected=snap['debt_cash_collected'],
        debt_mobile_collected=snap['debt_mobile_collected'],
        debt_bank_collected=snap['debt_bank_collected'],
        sale_count=snap['sale_count'],
        debt_payment_count=snap['payment_count'],
        cash_paid_out=cash_paid_out,
        expected_cash=expected,
        counted_cash=counted_cash,
        difference=difference,
        notes=(notes or '').strip(),
        closed_by=user,
    )


@transaction.atomic
def amend_cash_closing(*, closing, opening_float, cash_paid_out, counted_cash, notes, amendment_reason, user):
    closing=CashClosing.objects.select_for_update().get(pk=closing.pk)
    reason=(amendment_reason or '').strip()
    if not reason:
        raise StockError('Enter an amendment reason.')
    opening_float=money(opening_float)
    cash_paid_out=money(cash_paid_out)
    counted_cash=money(counted_cash)
    if opening_float<0 or cash_paid_out<0 or counted_cash<0:
        raise StockError('Opening float, cash paid out and counted cash cannot be negative.')
    available_cash=money(opening_float+closing.cash_sales+closing.debt_cash_collected)
    if cash_paid_out>available_cash:
        raise StockError(f'Cash paid out cannot exceed available cash of {available_cash} TZS.')
    expected=money(available_cash-cash_paid_out)
    closing.opening_float=opening_float
    closing.cash_paid_out=cash_paid_out
    closing.expected_cash=expected
    closing.counted_cash=counted_cash
    closing.difference=money(counted_cash-expected)
    closing.notes=(notes or '').strip()
    closing.amended_by=user
    closing.amended_at=timezone.now()
    closing.amendment_reason=reason
    closing.save(update_fields=[
        'opening_float','cash_paid_out','expected_cash','counted_cash','difference','notes',
        'amended_by','amended_at','amendment_reason',
    ])
    return closing


def estimate_product_unit_cost(product):
    batches=list(product.batches.filter(quantity_remaining_base__gt=0))
    qty=sum((b.quantity_remaining_base for b in batches),ZERO)
    value=sum((b.quantity_remaining_base*b.unit_cost_base for b in batches),ZERO)
    if qty>0:
        return value/qty
    latest=product.batches.order_by('-received_at','-id').first()
    return latest.unit_cost_base if latest else ZERO


@transaction.atomic
def create_stocktake(*, count_date, notes, user):
    if count_date>timezone.localdate():
        raise StockError('Stocktake date cannot be in the future.')
    stocktake=Stocktake.objects.create(count_date=count_date,notes=(notes or '').strip(),created_by=user)
    products=Product.objects.filter(is_active=True).order_by('name').prefetch_related('batches')
    lines=[]
    for product in products:
        lines.append(StocktakeLine(
            stocktake=stocktake,
            product=product,
            system_qty_base=product.stock_qty,
            count_unit_name=product.base_unit,
            count_unit_symbol=product.base_unit,
            count_conversion=Decimal('1'),
            unit_cost_base=estimate_product_unit_cost(product),
        ))
    StocktakeLine.objects.bulk_create(lines)
    return stocktake


@transaction.atomic
def refresh_stocktake_snapshot(*, stocktake):
    stocktake=Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status!='draft':
        raise StockError('Only a draft stocktake can be refreshed.')
    for line in stocktake.lines.select_related('product').order_by('product_id'):
        product=Product.objects.select_for_update().get(pk=line.product_id)
        line.system_qty_base=product.stock_qty
        line.unit_cost_base=estimate_product_unit_cost(product)
        if line.counted_qty_base is not None:
            line.difference_base=line.counted_qty_base-line.system_qty_base
        line.save(update_fields=['system_qty_base','unit_cost_base','difference_base'])
    return stocktake


@transaction.atomic
def post_stocktake(*, stocktake, user):
    stocktake=Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status!='draft':
        raise StockError('Only a draft stocktake can be posted.')
    lines=list(stocktake.lines.select_related('product').order_by('product_id'))
    if not lines:
        raise StockError('This stocktake has no products.')
    missing=[line.product.name for line in lines if line.counted_qty_base is None]
    if missing:
        preview=', '.join(missing[:4])
        suffix='…' if len(missing)>4 else ''
        raise StockError(f'Enter a physical count for every product. Missing: {preview}{suffix}')

    locked_products={}
    stale=[]
    for line in lines:
        product=Product.objects.select_for_update().get(pk=line.product_id)
        locked_products[line.product_id]=product
        current=product.batches.aggregate(v=Sum('quantity_remaining_base'))['v'] or ZERO
        if current!=line.system_qty_base:
            stale.append(f'{product.name} ({current} {product.base_unit})')
    if stale:
        preview=', '.join(stale[:4])
        suffix='…' if len(stale)>4 else ''
        raise StockError(f'Stock changed while counting. Refresh the stocktake snapshot first: {preview}{suffix}')

    for line in lines:
        product=locked_products[line.product_id]
        diff=line.counted_qty_base-line.system_qty_base
        line.difference_base=diff
        adjustment=None
        reason=f'Stocktake {stocktake.reference}'
        if stocktake.notes:
            reason=f'{reason}: {stocktake.notes}'
        reason=reason[:180]
        if diff<0:
            adjustment=decrease_stock_fifo(product=product,quantity=abs(diff),user=user,reason=reason)
        elif diff>0:
            adjustment=increase_stock(
                product=product,
                quantity=diff,
                unit_cost_base=line.unit_cost_base,
                user=user,
                reason=reason,
            )
        line.adjustment=adjustment
        line.save(update_fields=['difference_base','adjustment'])

    stocktake.status='posted'
    stocktake.posted_by=user
    stocktake.posted_at=timezone.now()
    stocktake.save(update_fields=['status','posted_by','posted_at'])
    return stocktake


@transaction.atomic
def cancel_stocktake(*, stocktake, user):
    stocktake=Stocktake.objects.select_for_update().get(pk=stocktake.pk)
    if stocktake.status!='draft':
        raise StockError('Only a draft stocktake can be cancelled.')
    stocktake.status='cancelled'
    stocktake.cancelled_by=user
    stocktake.cancelled_at=timezone.now()
    stocktake.save(update_fields=['status','cancelled_by','cancelled_at'])
    return stocktake
