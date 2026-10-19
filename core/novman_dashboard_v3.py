"""Branch-scoped Novman V2 dashboard calculations.

Does not create/update business records. Chart data matches the existing local
canvas renderer in static/core/app.js, so no external scripts are needed.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import (
    CustomerPayment, Expense, Product, Purchase, Sale, SaleItem, StockBatch,
)
from .novman_branch import current_branch

ZERO = Decimal('0')
MAX_DASHBOARD_DAYS = 366


def _date(value, default):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return default


def _sum(qs, field):
    return qs.aggregate(value=Sum(field))['value'] or ZERO


def _comparison(current, previous, today_only):
    label = 'yesterday' if today_only else 'previous period'
    if previous == ZERO:
        return f'No change from {label}' if current == ZERO else f'No sales in {label}'
    change = ((current - previous) / abs(previous)) * Decimal('100')
    return f'{abs(change):.1f}% {"up" if change >= 0 else "down"} from {label}'


def dashboard_context(request):
    """Build the existing Jane-style dashboard view with strict branch scoping."""
    branch = current_branch(request)
    today = timezone.localdate()
    start = _date(request.GET.get('start'), today)
    end = _date(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    # Avoid rendering huge, expensive daily charts for accidental multi-year filters.
    if end > today:
        end = today
    if start > end:
        start = end
    limited = (end - start).days >= MAX_DASHBOARD_DAYS
    if limited:
        start = end - timedelta(days=MAX_DASHBOARD_DAYS - 1)

    period_days = (end - start).days + 1
    today_only = start == today and end == today
    single = start == end
    last_7_start = today - timedelta(days=6)
    month_start = today.replace(day=1)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=period_days-1)

    if today_only:
        period_label = 'Today'
        period_subtitle = today.strftime('%d %b %Y')
    elif single:
        period_label = start.strftime('%d %b %Y')
        period_subtitle = 'Single day'
    elif start.year == end.year:
        period_label = f'{start:%d %b} – {end:%d %b %Y}'
        period_subtitle = f'{period_days} days'
    else:
        period_label = f'{start:%d %b %Y} – {end:%d %b %Y}'
        period_subtitle = f'{period_days} days'

    # Money totals: completed sales only, on the selected branch only.
    sales = Sale.objects.filter(branch=branch, status='completed', sold_at__date__range=(start, end))
    totals = sales.aggregate(
        revenue=Sum('grand_total'), gross=Sum('profit_total'), cogs=Sum('cogs_total'),
        item_discounts=Sum('item_discount_total'), bill_discounts=Sum('sale_discount'),
    )
    revenue = totals['revenue'] or ZERO
    gross = totals['gross'] or ZERO
    cogs = totals['cogs'] or ZERO
    discounts = (totals['item_discounts'] or ZERO) + (totals['bill_discounts'] or ZERO)
    expenses_qs = Expense.objects.filter(branch=branch, expense_date__range=(start, end))
    expenses = _sum(expenses_qs, 'amount')
    purchases = _sum(
        Purchase.objects.filter(branch=branch, purchased_at__date__range=(start, end)),
        'items__total_cost',
    )
    previous = Sale.objects.filter(
        branch=branch, status='completed', sold_at__date__range=(prior_start, prior_end)
    ).aggregate(revenue=Sum('grand_total'), gross=Sum('profit_total'))

    count = sales.count()
    margin = gross * Decimal('100') / revenue if revenue else ZERO
    average_sale = revenue / count if count else ZERO
    voided = Sale.objects.filter(branch=branch, status='voided', sold_at__date__range=(start, end)).count()
    debt_sales = _sum(sales.filter(payment_method='debt'), 'grand_total')
    debt_payments_period = CustomerPayment.objects.filter(
        branch=branch, is_voided=False, paid_at__date__range=(start, end)
    )
    debt_collected = _sum(debt_payments_period, 'amount')

    # Branch-specific debt position: never use Customer.current_balance because
    # that property includes sales and repayments from other branches.
    credit_by_customer = {
        row['customer_id']: row['balance'] or ZERO
        for row in Sale.objects.filter(branch=branch, status='completed', payment_method='debt')
        .values('customer_id').annotate(balance=Sum('grand_total'))
    }
    paid_by_customer = {
        row['customer_id']: row['balance'] or ZERO
        for row in CustomerPayment.objects.filter(branch=branch, is_voided=False)
        .values('customer_id').annotate(balance=Sum('amount'))
    }
    due_by_customer = {
        customer_id: max(ZERO, due - paid_by_customer.get(customer_id, ZERO))
        for customer_id, due in credit_by_customer.items()
    }
    outstanding_debt = sum(due_by_customer.values(), ZERO)
    debtor_count = sum(1 for due in due_by_customer.values() if due > ZERO)

    # Current inventory: FIFO remaining batch costs, not selling prices.
    # A globally shared catalogue does not mean a shared stock balance.
    qty = defaultdict(lambda: ZERO)
    stock_by_product = defaultdict(lambda: ZERO)
    for batch in (StockBatch.objects.filter(branch=branch, quantity_remaining_base__gt=0)
                  .select_related('product__category')):
        qty[batch.product_id] += batch.quantity_remaining_base
        stock_by_product[batch.product_id] += batch.quantity_remaining_base * batch.unit_cost_base
    stock_value = sum(stock_by_product.values(), ZERO)
    stock_qty = sum(qty.values(), ZERO)
    products = list(Product.objects.filter(is_active=True).select_related('category'))
    low_products = []
    category_map = {}
    in_stock_count = 0
    out_count = 0
    for product in products:
        product.dashboard_stock_qty = qty[product.pk]
        product.dashboard_stock_value = stock_by_product[product.pk]
        if product.dashboard_stock_qty > ZERO:
            in_stock_count += 1
            cat = category_map.setdefault(product.category_id, {
                'name': product.category.name, 'value': ZERO, 'products': 0,
            })
            cat['value'] += product.dashboard_stock_value
            cat['products'] += 1
        else:
            out_count += 1
        if product.dashboard_stock_qty <= product.low_stock_level:
            low_products.append(product)
    low_products.sort(key=lambda p: (p.dashboard_stock_qty, p.name.casefold(), p.pk))
    category_stock = sorted(category_map.values(), key=lambda x: x['value'], reverse=True)[:6]
    max_cat = max((r['value'] for r in category_stock), default=ZERO)
    for row in category_stock:
        row['percent'] = float(row['value'] * 100 / max_cat) if max_cat else 0

    # Per-day chart. TruncDate follows Django's configured local timezone.
    sale_days = {
        row['day']: row
        for row in sales.annotate(day=TruncDate('sold_at')).values('day')
        .annotate(revenue=Sum('grand_total'), gross=Sum('profit_total'))
    }
    expense_days = {
        row['expense_date']: row['total'] or ZERO
        for row in expenses_qs.values('expense_date').annotate(total=Sum('amount'))
    }
    daily = []
    for offset in range(period_days):
        day = start + timedelta(days=offset)
        row = sale_days.get(day, {})
        daily.append({
            'label': day.strftime('%d %b'),
            'revenue': float(row.get('revenue') or 0),
            'profit': float(row.get('gross') or 0),
            'expenses': float(expense_days.get(day, ZERO)),
        })

    pay_rows = {
        r['payment_method']: r
        for r in sales.values('payment_method').annotate(amount=Sum('grand_total'), count=Count('pk'))
    }
    payment_summary = [
        {'key': method, 'label': label,
         'amount': pay_rows.get(method, {}).get('amount') or ZERO,
         'count': pay_rows.get(method, {}).get('count') or 0}
        for method, label in Sale.PAYMENT_CHOICES
    ]

    # Product ID in grouping preserves products with identical names.
    top_products = list(
        SaleItem.objects.filter(sale__branch=branch, sale__status='completed',
                                sale__sold_at__date__range=(start, end))
        .values('product_id', 'product__name', 'product__base_unit')
        .annotate(revenue=Sum('final_total'), quantity=Sum('base_quantity'))
        .order_by('-revenue', 'product_id')[:6]
    )
    top_max = max((r['revenue'] or ZERO for r in top_products), default=ZERO)
    for row in top_products:
        row['percent'] = float((row['revenue'] or ZERO) * 100 / top_max) if top_max else 0

    chart = {
        'daily': daily,
        'payments': [
            {'key': r['key'], 'label': r['label'], 'value': float(r['amount']), 'count': r['count']}
            for r in payment_summary
        ],
    }
    recent_sales = (Sale.objects.filter(branch=branch, sold_at__date__range=(start, end))
                    .select_related('cashier').order_by('-sold_at', '-pk')[:8])

    return {
        'branch': branch,
        'period_label': period_label, 'period_subtitle': period_subtitle,
        'filter_start': start.isoformat(), 'filter_end': end.isoformat(),
        'today_iso': today.isoformat(), 'last_7_start': last_7_start.isoformat(),
        'month_start': month_start.isoformat(), 'is_today_filter': today_only,
        'is_last_7_filter': start == last_7_start and end == today,
        'is_month_filter': start == month_start and end == today,
        'range_limited': limited,
        'today_sales': count, 'sales_count': count, 'revenue': revenue,
        'gross_profit': gross, 'net_today': gross - expenses,
        'expenses_today': expenses, 'cogs_today': cogs,
        'purchases_today': purchases, 'discounts_today': discounts,
        'average_sale': average_sale, 'margin_pct': margin,
        'voided_today': voided, 'revenue_compare': _comparison(revenue, previous['revenue'] or ZERO, today_only),
        'profit_compare': _comparison(gross, previous['gross'] or ZERO, today_only),
        'outstanding_debt': outstanding_debt, 'debtor_count': debtor_count,
        'debt_sales_period': debt_sales, 'debt_collected_period': debt_collected,
        'stock_value': stock_value, 'stock_qty': stock_qty,
        'active_products': len(products), 'in_stock_count': in_stock_count,
        'low_stock_count': len(low_products), 'out_of_stock_count': out_count,
        'low_products': low_products[:8], 'category_stock': category_stock,
        'top_products': top_products, 'payment_summary': payment_summary,
        'recent_sales': recent_sales, 'dashboard_chart_data': chart,
    }
