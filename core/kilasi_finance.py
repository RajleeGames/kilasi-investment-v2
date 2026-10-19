"""Phase 3A: read-only retail finance from existing Novman branch-scoped ledgers.

No records are created, edited or imported by these views. Reports include only
completed sales in the requested period; historical debt balance is lifetime
for the selected branch and is intentionally a distinct metric.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .decorators import admin_required
from .models import CustomerPayment, Expense, Product, Sale, SaleItem
from .novman_branch import current_branch, is_admin

ZERO = Decimal('0')
MAX_DAYS = 366


def _date(value, fallback):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback


def _total(qs, field):
    return qs.aggregate(total=Sum(field))['total'] or ZERO


@admin_required
@require_GET
def overview(request):
    """A finance view whose filters and debt balances never cross branch boundaries."""
    branch = current_branch(request)
    today = timezone.localdate()
    first = today.replace(day=1)
    start = _date(request.GET.get('start'), first)
    end = _date(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    if end > today:
        end = today
    if start > today:
        start = today
    limited = False
    if (end - start).days >= MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS - 1)
        limited = True

    sales = Sale.objects.filter(
        branch=branch, status='completed', sold_at__date__range=(start, end)
    )
    all_sales = Sale.objects.filter(branch=branch, sold_at__date__range=(start, end))
    expenses_qs = Expense.objects.filter(branch=branch, expense_date__range=(start, end))
    payments_qs = CustomerPayment.objects.filter(
        branch=branch, is_voided=False, paid_at__date__range=(start, end)
    )

    sums = sales.aggregate(
        revenue=Sum('grand_total'), cogs=Sum('cogs_total'),
        gross=Sum('profit_total'), item_discount=Sum('item_discount_total'),
        sale_discount=Sum('sale_discount'),
    )
    revenue = sums['revenue'] or ZERO
    cogs = sums['cogs'] or ZERO
    gross = sums['gross'] or ZERO
    expenses = _total(expenses_qs, 'amount')
    debt_collected = _total(payments_qs, 'amount')
    debt_sales = _total(sales.filter(payment_method='debt'), 'grand_total')
    non_debt_sales = revenue - debt_sales
    receipts_count = sales.count()
    void_count = all_sales.filter(status='voided').count()
    net = gross - expenses

    pay_aggregates = {
        r['payment_method']: r
        for r in sales.values('payment_method').annotate(
            amount=Sum('grand_total'), count=Count('id')
        )
    }
    payment_summary = [
        {
            'key': method, 'label': label,
            'amount': pay_aggregates.get(method, {}).get('amount') or ZERO,
            'count': pay_aggregates.get(method, {}).get('count') or 0,
        }
        for method, label in Sale.PAYMENT_CHOICES
    ]

    # Customer-wide balances on the Customer model may span branches.
    # Compute this branch's lifetime debt and payments independently.
    debt_map = {
        row['customer_id']: row['balance'] or ZERO
        for row in Sale.objects.filter(
            branch=branch, status='completed', payment_method='debt',
            customer_id__isnull=False,
        ).values('customer_id').annotate(balance=Sum('grand_total'))
    }
    paid_map = {
        row['customer_id']: row['balance'] or ZERO
        for row in CustomerPayment.objects.filter(
            branch=branch, is_voided=False
        ).values('customer_id').annotate(balance=Sum('amount'))
    }
    outstanding = sum(
        (max(ZERO, amount - paid_map.get(customer_id, ZERO))
         for customer_id, amount in debt_map.items()), ZERO
    )
    customers_due = sum(
        1 for customer_id, amount in debt_map.items()
        if amount - paid_map.get(customer_id, ZERO) > ZERO
    )
    unlinked_credit_sales = Sale.objects.filter(
        branch=branch, status='completed', payment_method='debt',
        customer_id__isnull=True,
    ).count()

    division_totals = {
        row['product__retail_division']: row
        for row in SaleItem.objects.filter(sale__in=sales)
        .values('product__retail_division')
        .annotate(value=Sum('final_total'), quantity=Sum('base_quantity'))
    }
    division_rows = [
        {'key': key, 'label': label,
         'amount': division_totals.get(key, {}).get('value') or ZERO,
         'quantity': division_totals.get(key, {}).get('quantity') or ZERO}
        for key, label in Product.RETAIL_DIVISION_CHOICES
    ]
    # Item totals precede bill-level discounts; displayed separately.

    sale_days = {
        r['day']: r
        for r in sales.annotate(day=TruncDate('sold_at'))
        .values('day').annotate(
            revenue=Sum('grand_total'), gross=Sum('profit_total')
        )
    }
    expense_days = {
        r['expense_date']: r['total'] or ZERO
        for r in expenses_qs.values('expense_date').annotate(total=Sum('amount'))
    }
    day_count = (end - start).days + 1
    chart = {
        'daily': [
            {
                'label': (start + timedelta(days=n)).strftime('%d %b'),
                'revenue': float(sale_days.get(start + timedelta(days=n), {}).get('revenue') or 0),
                'profit': float(sale_days.get(start + timedelta(days=n), {}).get('gross') or 0),
                'expenses': float(expense_days.get(start + timedelta(days=n), ZERO)),
            }
            for n in range(day_count)
        ],
        'payments': [
            {'key': row['key'], 'label': row['label'],
             'value': float(row['amount']), 'count': row['count']}
            for row in payment_summary
        ],
    }

    q = (request.GET.get('q') or '').strip()[:100]
    recent = all_sales.select_related('cashier', 'customer')
    if q:
        recent = recent.filter(
            Q(receipt_no__icontains=q) | Q(customer_name__icontains=q)
            | Q(customer__name__icontains=q) | Q(cashier__username__icontains=q)
        )
    recent = recent.order_by('-sold_at', '-pk')[:30]
    return render(request, 'kilasi_finance_overview.html', {
        'branch': branch,
        'start': start.isoformat(), 'end': end.isoformat(),
        'today_iso': today.isoformat(), 'month_start': first.isoformat(),
        'week_start': (today - timedelta(days=6)).isoformat(),
        'range_limited': limited, 'q': q,
        'revenue': revenue, 'cogs': cogs, 'gross': gross,
        'expenses': expenses, 'net': net,
        'receipts_count': receipts_count, 'void_count': void_count,
        'average_sale': revenue / receipts_count if receipts_count else ZERO,
        'item_discount': sums['item_discount'] or ZERO,
        'sale_discount': sums['sale_discount'] or ZERO,
        'non_debt_sales': non_debt_sales,
        'debt_sales': debt_sales, 'debt_collected': debt_collected,
        'outstanding': outstanding, 'customers_due': customers_due,
        'unlinked_credit_sales': unlinked_credit_sales,
        'division_rows': division_rows, 'payment_summary': payment_summary,
        'recent_sales': recent, 'dashboard_chart_data': chart,
    })


@login_required
@require_GET
def sales_document(request, pk):
    """Read-only printable copy of an EXISTING Sale, not a new legal/tax invoice."""
    branch = current_branch(request)
    sale = get_object_or_404(
        Sale.objects.select_related('customer', 'cashier', 'branch')
        .prefetch_related('items__product', 'items__unit'),
        pk=pk, branch=branch,
    )
    if not is_admin(request.user) and sale.cashier_id != request.user.pk:
        return HttpResponseForbidden('This sale is not assigned to you.')
    return render(request, 'kilasi_sales_document.html', {
        'sale': sale, 'branch': branch,
        'total_discounts': sale.item_discount_total + sale.sale_discount,
    })
