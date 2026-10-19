"""Branch-scoped atomic posting; never call this module while importing legacy rows."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.models import Product, StockBatch, Customer
from core import novman_stock
from .models import TopupTransaction, TopupConsumption, UsedTopupStock, UsedTopupSale

D2 = Decimal('0.01')


def amount(value):
    try:
        result = Decimal(str(value)).quantize(D2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Enter a valid amount.')
    if not result.is_finite() or result < 0:
        raise ValidationError('Amounts must be non-negative and finite.')
    return result


def find_customer(name, phone):
    name = (name or '').strip()
    phone = (phone or '').strip()
    if not name and not phone:
        return None
    if phone:
        c = Customer.objects.filter(phone__iexact=phone, is_active=True).order_by('pk').first()
        if c:
            return c
    # Never merge by name only: multiple people can have the same name.
    return Customer.objects.create(name=name or 'Customer', phone=phone)


@transaction.atomic
def post_topup(*, branch, product_id, quantity, used_name, category, condition,
               used_cost, cash_added, customer_name='', customer_phone='', note='', user):
    """One branch-specific FIFO depletion plus one used-stock receipt, or nothing."""
    try:
        qty = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('Invalid stock quantity.')
    if not qty.is_finite() or qty <= 0 or qty.as_tuple().exponent < -3:
        raise ValidationError('Enter a positive quantity with up to three decimal places.')
    cost, cash = amount(used_cost), amount(cash_added)
    if not used_name.strip():
        raise ValidationError('Used item name is required.')
    if category not in ('electronics', 'furniture', 'magodoro'):
        raise ValidationError('Choose a used item division.')
    product = Product.objects.select_for_update().get(pk=product_id, is_active=True)
    if product.base_unit != 'PCS' or qty != qty.to_integral_value():
        raise ValidationError('Top-Up Phase 6A supports whole-piece products only.')
    customer = find_customer(customer_name, customer_phone)
    tx = TopupTransaction.objects.create(
        branch=branch, customer=customer, customer_name=customer_name.strip(),
        customer_phone=customer_phone.strip(), new_product=product,
        new_product_qty=qty, used_item_name=used_name.strip(),
        used_item_category=category, used_item_condition=condition.strip(),
        used_item_buying_price=cost, addon_amount=cash, note=note.strip(), created_by=user,
    )
    # _consume uses the actual branch and FIFO costing, with row update checks.
    allocations = novman_stock._consume(product, branch, qty)
    cogs = Decimal('0')
    for batch, taken in allocations:
        line_cost = (taken * batch.unit_cost_base).quantize(D2, rounding=ROUND_HALF_UP)
        TopupConsumption.objects.create(transaction=tx, batch=batch,
            quantity_base=taken, unit_cost_base=batch.unit_cost_base, cost_total=line_cost)
        cogs += line_cost
    tx.outgoing_cogs = cogs
    tx.save(update_fields=['outgoing_cogs'])
    UsedTopupStock.objects.create(transaction=tx, branch=branch,
        item_name=used_name.strip(), item_category=category,
        condition=condition.strip(), buying_price=cost,
        note=f'Received via top-up #{tx.pk}')
    return tx


@transaction.atomic
def sell_used_stock(*, branch, item_id, sold_price, customer_name='', customer_phone='', note='', user):
    item = UsedTopupStock.objects.select_for_update().get(pk=item_id, branch=branch)
    if item.status != 'available' or UsedTopupSale.objects.filter(used_stock=item).exists():
        raise ValidationError('This used item has already been sold.')
    price = amount(sold_price)
    customer = find_customer(customer_name, customer_phone)
    sale = UsedTopupSale.objects.create(used_stock=item, customer=customer,
        customer_name=customer_name.strip(), customer_phone=customer_phone.strip(),
        branch=branch, sold_price=price, note=note.strip(), sold_by=user)
    item.status = 'sold'
    item.save(update_fields=['status'])
    return sale
