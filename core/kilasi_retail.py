"""Kilasi retail divisions: informational, branch-scoped stock dashboard.

No data mutations. Monetary projections are illustrative and use the default
selling unit, never historical sales values or a claim of realized profit.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR

from django.db.models import Sum
from django.shortcuts import render

from .decorators import admin_required
from .models import Product, StockBatch
from .novman_branch import current_branch

ZERO = Decimal('0')


@admin_required
def overview(request):
    branch = current_branch(request)
    divisions = {
        code: {
            'code': code, 'name': label, 'products': 0, 'in_stock': 0,
            'low_stock': 0, 'stock_value': ZERO, 'selling_value': ZERO,
            'without_price': 0,
        }
        for code, label in Product.RETAIL_DIVISION_CHOICES
    }
    stock_quantities = defaultdict(lambda: ZERO)
    stock_costs = defaultdict(lambda: ZERO)
    # ONLY the user's selected branch is used in all stock calculations.
    batches = StockBatch.objects.filter(
        branch=branch, quantity_remaining_base__gt=0,
    ).values('product_id').annotate(
        qty=Sum('quantity_remaining_base'),
    )
    # Aggregating money cannot multiply qty by varying batch unit costs in
    # the above queryset, so read batch valuations separately.
    for row in batches:
        stock_quantities[row['product_id']] = row['qty'] or ZERO
    for batch in StockBatch.objects.filter(
        branch=branch, quantity_remaining_base__gt=0
    ).only('product_id', 'quantity_remaining_base', 'unit_cost_base'):
        stock_costs[batch.product_id] += (
            batch.quantity_remaining_base * batch.unit_cost_base
        )

    for product in Product.objects.prefetch_related('selling_units').all():
        item = divisions.get(product.retail_division, divisions['other'])
        qty = stock_quantities[product.pk]
        value = stock_costs[product.pk]
        if product.is_active:
            item['products'] += 1
            if qty > ZERO:
                item['in_stock'] += 1
            if qty <= product.low_stock_level:
                item['low_stock'] += 1
        item['stock_value'] += value

        if qty <= ZERO:
            continue
        active_units = [u for u in product.selling_units.all()
                        if u.is_active and u.conversion_to_base > ZERO]
        unit = next((u for u in active_units if u.is_default), None)
        if unit is None:
            unit = active_units[0] if active_units else None
        if unit is None:
            item['without_price'] += 1
            continue
        # Whole sellable units only; never assume a partial retail unit is sold.
        sellable_units = (qty / unit.conversion_to_base).to_integral_value(
            rounding=ROUND_FLOOR
        )
        item['selling_value'] += sellable_units * unit.selling_price

    rows = list(divisions.values())
    total_cost = sum((x['stock_value'] for x in rows), ZERO)
    total_selling = sum((x['selling_value'] for x in rows), ZERO)
    return render(request, 'kilasi_retail_overview.html', {
        'branch': branch,
        'divisions': rows,
        'total_cost': total_cost,
        'total_selling': total_selling,
        'active_products': sum(x['products'] for x in rows),
        'stocked_products': sum(x['in_stock'] for x in rows),
        'low_stock': sum(x['low_stock'] for x in rows),
        'unpriced_products': sum(x['without_price'] for x in rows),
    })
