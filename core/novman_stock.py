"""Branch-safe stock mutations for Novman V2.

All quantities are in product.base_unit and all prices in TZS. Historical
legacy batch identifiers remain unchanged. Never use global Product.stock_qty
for branch-sensitive operations.
"""
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from .models import (
    Sale, SaleItem, SaleAllocation, StockBatch, StockAdjustment, Purchase,
    PurchaseItem, Customer, Product, ProductUnit, CustomerPayment,
    Stocktake, StocktakeLine, StockTransfer, StockTransferLine,
)
from .services import D, money, StockError

ZERO = Decimal('0')


def branch_quantity(product, branch):
    return (StockBatch.objects.filter(product=product, branch=branch)
            .aggregate(q=Sum('quantity_remaining_base'))['q'] or ZERO)


def branch_value(product, branch):
    return sum((x.quantity_remaining_base * x.unit_cost_base for x in
                StockBatch.objects.filter(product=product, branch=branch)), ZERO)


def _consume(product, branch, qty):
    """Allocate available batches FIFO with updates protected against overdraw."""
    remaining = D(qty)
    if remaining <= 0:
        raise StockError('Quantity must be greater than zero.')
    allocations = []
    for batch in (StockBatch.objects.select_for_update()
                  .filter(product=product, branch=branch, quantity_remaining_base__gt=0)
                  .order_by('received_at', 'id')):
        if remaining <= 0:
            break
        take = min(remaining, batch.quantity_remaining_base)
        updated = (StockBatch.objects.filter(pk=batch.pk, quantity_remaining_base__gte=take)
                   .update(quantity_remaining_base=F('quantity_remaining_base') - take))
        if updated != 1:
            raise StockError('Concurrent stock change detected. Retry.')
        allocations.append((batch, take))
        remaining -= take
    if remaining > 0:
        raise StockError(f'Not enough stock for {product.name} in {branch.name}. Missing: {remaining} {product.base_unit}.')
    return allocations


@transaction.atomic
def opening_stock(*, product, branch, quantity_base, unit_cost_base, reference, user):
    qty = D(quantity_base)
    cost = D(unit_cost_base)
    if qty <= 0 or cost < 0:
        raise StockError('Enter a valid positive quantity and non-negative cost.')
    return StockBatch.objects.create(product=product, branch=branch, source='OPENING',
        reference=reference or f'Opening stock - {branch.name}',
        quantity_received_base=qty, quantity_remaining_base=qty,
        unit_cost_base=cost, received_at=timezone.now(), created_by=user)


@transaction.atomic
def purchase_stock(*, product, branch, unit, quantity, cost_per_unit, supplier_name,
                   invoice_number, notes, user):
    qty = D(quantity); cost = D(cost_per_unit)
    if qty <= 0 or cost < 0 or unit.product_id != product.pk or not unit.is_active:
        raise StockError('Invalid purchase quantity, cost, or selling unit.')
    base_qty = qty * unit.conversion_to_base
    total = money(qty * cost)
    purchase = Purchase.objects.create(branch=branch, supplier_name=(supplier_name or ''),
        invoice_number=invoice_number or '', notes=notes or '', created_by=user)
    item = PurchaseItem.objects.create(purchase=purchase, product=product, unit=unit,
        quantity=qty, base_quantity=base_qty, cost_per_unit=cost,
        total_cost=total, unit_cost_base=total / base_qty)
    product.last_buying_price = money(cost / unit.conversion_to_base)
    product.save(update_fields=['last_buying_price'])
    StockBatch.objects.create(product=product, branch=branch, source='PURCHASE',
        purchase_item=item, reference=invoice_number or f'PUR-{purchase.pk:05d}',
        quantity_received_base=base_qty, quantity_remaining_base=base_qty,
        unit_cost_base=item.unit_cost_base, received_at=purchase.purchased_at, created_by=user)
    return purchase


@transaction.atomic
def sell(*, branch, cart, payment_method, customer_name, customer_phone, sale_discount, cashier):
    if not isinstance(cart, list) or not cart:
        raise StockError('Cart is empty.')
    if payment_method not in dict(Sale.PAYMENT_CHOICES):
        raise StockError('Invalid payment method.')
    discount_total = money(sale_discount)
    customer_name = (customer_name or '').strip()
    phone = (customer_phone or '').strip()
    customer = None
    if payment_method == 'debt':
        if not customer_name:
            raise StockError('Customer name is required for a debt sale.')
        if phone:
            customer = Customer.objects.filter(phone__iexact=phone, is_active=True).first()
        if customer is None:
            customer = Customer.objects.filter(name__iexact=customer_name, is_active=True).first()
        if customer is None:
            customer = Customer.objects.create(name=customer_name, phone=phone)
    sale = Sale.objects.create(branch=branch, payment_method=payment_method,
        customer=customer, customer_name=customer_name, sale_discount=discount_total,
        cashier=cashier)
    subtotal = ZERO; item_discounts = ZERO; cogs = ZERO
    for row in cart:
        product = Product.objects.select_for_update().get(pk=int(row['product_id']), is_active=True)
        unit = ProductUnit.objects.get(pk=int(row['unit_id']), product=product, is_active=True)
        qty = D(row.get('quantity'))
        discount = money(row.get('discount', 0))
        if qty <= 0:
            raise StockError(f'Invalid quantity for {product.name}.')
        base_qty = qty * unit.conversion_to_base
        line_subtotal = money(qty * unit.selling_price)
        if discount < 0 or discount > line_subtotal:
            raise StockError(f'Invalid discount for {product.name}.')
        final = money(line_subtotal - discount)
        item = SaleItem.objects.create(sale=sale, product=product, unit=unit,
            quantity=qty, conversion_to_base=unit.conversion_to_base,
            base_quantity=base_qty, normal_unit_price=unit.selling_price,
            line_subtotal=line_subtotal, discount_amount=discount, final_total=final)
        item_cost = ZERO
        for batch, taken in _consume(product, branch, base_qty):
            allocation_cost = money(taken * batch.unit_cost_base)
            SaleAllocation.objects.create(sale_item=item, batch=batch,
                quantity_base=taken, unit_cost_base=batch.unit_cost_base,
                cost_total=allocation_cost)
            item_cost += allocation_cost
        item.cost_total = money(item_cost)
        item.profit_before_sale_discount = money(final - item.cost_total)
        item.save(update_fields=['cost_total', 'profit_before_sale_discount'])
        subtotal += line_subtotal; item_discounts += discount; cogs += item.cost_total
    total_before = money(subtotal - item_discounts)
    if discount_total < 0 or discount_total > total_before:
        raise StockError('Bill discount cannot exceed bill total.')
    sale.subtotal = money(subtotal)
    sale.item_discount_total = money(item_discounts)
    sale.grand_total = money(total_before - discount_total)
    sale.cogs_total = money(cogs)
    sale.profit_total = money(sale.grand_total - cogs)
    sale.save(update_fields=['subtotal', 'item_discount_total', 'grand_total', 'cogs_total', 'profit_total'])
    return sale


@transaction.atomic
def adjust(*, product, branch, quantity, increase, unit_cost_base, user, reason):
    qty = D(quantity)
    if qty <= 0:
        raise StockError('Quantity must be positive.')
    if increase:
        cost = D(unit_cost_base)
        if cost < 0:
            raise StockError('Buying cost cannot be negative.')
        opening_stock(product=product, branch=branch, quantity_base=qty,
            unit_cost_base=cost, reference=f'Adjustment: {(reason or "").strip()}', user=user)
    else:
        _consume(product, branch, qty)
    return StockAdjustment.objects.create(branch=branch, product=product,
        adjustment_type='increase' if increase else 'decrease',
        quantity_base=qty, reason=(reason or 'Stock adjustment')[:180], created_by=user)


@transaction.atomic
def transfer(*, source, destination, product, quantity, user, notes=''):
    qty = D(quantity)
    if source.pk == destination.pk or qty <= 0:
        raise StockError('Choose two different branches and a positive quantity.')
    if not (source.is_active and destination.is_active):
        raise StockError('Both branches must be active.')
    tr = StockTransfer.objects.create(source_branch=source, destination_branch=destination,
        product=product, quantity_base=qty, created_by=user, notes=(notes or '')[:240])
    for batch, taken in _consume(product, source, qty):
        dst_batch = StockBatch.objects.create(product=product, branch=destination,
            source='OPENING', reference=f'Transfer #{tr.pk} from {source.name}',
            quantity_received_base=taken, quantity_remaining_base=taken,
            unit_cost_base=batch.unit_cost_base, received_at=timezone.now(), created_by=user)
        StockTransferLine.objects.create(transfer=tr, source_batch=batch,
            destination_batch=dst_batch, quantity_base=taken,
            unit_cost_base=batch.unit_cost_base)
    return tr


@transaction.atomic
def create_branch_stocktake(*, branch, date, notes, user):
    if date > timezone.localdate():
        raise StockError('Cannot stocktake future date.')
    session = Stocktake.objects.create(branch=branch, count_date=date, notes=notes or '', created_by=user)
    lines = []
    for product in Product.objects.filter(is_active=True).order_by('name'):
        qty = branch_quantity(product, branch)
        value = branch_value(product, branch)
        lines.append(StocktakeLine(stocktake=session, product=product,
            system_qty_base=qty, count_unit_name=product.base_unit,
            count_unit_symbol=product.base_unit, count_conversion=1,
            unit_cost_base=value / qty if qty else product.last_buying_price))
    StocktakeLine.objects.bulk_create(lines)
    return session


@transaction.atomic
def refresh_branch_stocktake(*, stocktake):
    if stocktake.status != 'draft':
        raise StockError('Only draft stocktakes can be refreshed.')
    for line in stocktake.lines.select_related('product'):
        qty = branch_quantity(line.product, stocktake.branch)
        value = branch_value(line.product, stocktake.branch)
        line.system_qty_base = qty
        line.unit_cost_base = value / qty if qty else line.product.last_buying_price
        line.difference_base = ((line.counted_qty_base - qty)
                                if line.counted_qty_base is not None else ZERO)
        line.save(update_fields=['system_qty_base', 'unit_cost_base', 'difference_base'])
    return stocktake


@transaction.atomic
def post_branch_stocktake(*, stocktake, user):
    if stocktake.status != 'draft':
        raise StockError('Only draft stocktakes can be posted.')
    lines = list(stocktake.lines.select_related('product'))
    if not lines or any(x.counted_qty_base is None for x in lines):
        raise StockError('Count every product before posting.')
    for line in lines:
        actual = branch_quantity(line.product, stocktake.branch)
        if actual != line.system_qty_base:
            raise StockError(f'Stock changed for {line.product.name}; refresh snapshot.')
    for line in lines:
        diff = line.counted_qty_base - line.system_qty_base
        if diff:
            line.adjustment = adjust(product=line.product, branch=stocktake.branch,
                quantity=abs(diff), increase=diff > 0, unit_cost_base=line.unit_cost_base,
                user=user, reason=f'Stocktake {stocktake.reference}')
            line.difference_base = diff
            line.save(update_fields=['difference_base', 'adjustment'])
    stocktake.status = 'posted'; stocktake.posted_by = user; stocktake.posted_at = timezone.now()
    stocktake.save(update_fields=['status', 'posted_by', 'posted_at'])
    return stocktake
