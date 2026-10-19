import base64
import csv
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from Crypto.Hash import SHA512
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .decorators import admin_required
from .forms import (
    ProductForm, ProductUnitForm, CategoryForm, ExpenseForm,
    UserCreateForm, UserEditForm, ShopSettingsForm, PurchaseMetaForm, CustomerForm,
)
from .models import (
    Product, ProductUnit, Category, Sale, SaleItem, Purchase, Expense,
    StockBatch, StockAdjustment, Customer, CustomerPayment,
    CashClosing, Stocktake, StocktakeLine,
    PersonalReceivable, PersonalReceivablePayment,
)
from .services import (
    add_opening_stock, add_purchase, create_sale, void_sale,
    decrease_stock_fifo, increase_stock, StockError, D,
    record_customer_payment, void_customer_payment,
    calculate_cash_closing_snapshot, close_cash_day, amend_cash_closing,
    create_stocktake, refresh_stocktake_snapshot, post_stocktake, cancel_stocktake,
)


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def _is_admin_user(user):
    return bool(user.is_superuser or getattr(getattr(user, 'profile', None), 'role', '') == 'admin')


def _active_admin_count():
    return User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(profile__role='admin')
    ).distinct().count()


def _user_has_history(user):
    return any([
        user.sales.exists(),
        user.voided_sales.exists(),
        user.purchase_set.exists(),
        user.expense_set.exists(),
        user.stockbatch_set.exists(),
        user.stockadjustment_set.exists(),
        user.customer_payments_received.exists(),
        user.voided_customer_payments.exists(),
        user.cash_closings.exists(),
        user.cash_closings_recorded.exists(),
        user.cash_closings_amended.exists(),
        user.stocktakes_created.exists(),
        user.stocktakes_posted.exists(),
        user.stocktakes_cancelled.exists(),

        user.personal_receivables_created.exists(),
        user.personal_receivable_payments_received.exists(),
    ])



def _parse_date(value, fallback):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback


def _date_range_from_request(request, *, month_default=False):
    today=timezone.localdate()
    default_start=today.replace(day=1) if month_default else today
    start=_parse_date(request.GET.get('start'),default_start)
    end=_parse_date(request.GET.get('end'),today)
    if start>end:
        start,end=end,start
    return start,end


def _money_text(value):
    return f'{Decimal(value or 0):,.2f}'


def _qty_text(value):
    n=Decimal(value or 0)
    text=f'{n:f}'
    if '.' in text:
        text=text.rstrip('0').rstrip('.')
    return text


def _csv_bytes(headers, rows):
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def _export_payload(kind, start, end):
    if kind=='sales':
        qs=Sale.objects.filter(sold_at__date__range=(start,end)).select_related('cashier','customer').order_by('sold_at','id')
        headers=['Receipt','Date','Time','Cashier','Customer','Phone','Payment','Status','Subtotal','Item discounts','Bill discount','Total','COGS','Profit']
        rows=[[
            s.receipt_no,s.sold_at.date().isoformat(),s.sold_at.strftime('%H:%M:%S'),
            s.cashier.get_full_name() or s.cashier.username,s.customer_name or (s.customer.name if s.customer else ''),
            s.customer.phone if s.customer else '',s.get_payment_method_display(),s.get_status_display(),
            _money_text(s.subtotal),_money_text(s.item_discount_total),_money_text(s.sale_discount),
            _money_text(s.grand_total),_money_text(s.cogs_total),_money_text(s.profit_total),
        ] for s in qs]
        return headers,rows

    if kind=='stock':
        qs=Product.objects.select_related('category').prefetch_related('batches').order_by('name')
        headers=['Product','Category','Available','Base unit','Stock value','Low stock level','Status']
        rows=[[
            p.name,p.category.name,_qty_text(p.stock_qty),p.base_unit,_money_text(p.stock_value),
            _qty_text(p.low_stock_level),'Low' if p.is_low_stock else 'OK',
        ] for p in qs]
        return headers,rows

    if kind=='purchases':
        qs=Purchase.objects.filter(purchased_at__date__range=(start,end)).select_related('created_by').prefetch_related('items__product','items__unit').order_by('purchased_at','id')
        headers=['Purchase','Date','Supplier','Invoice','Product','Unit','Quantity','Base quantity','Cost per unit','Total cost','Recorded by']
        rows=[]
        for purchase in qs:
            for item in purchase.items.all():
                rows.append([
                    f'PUR-{purchase.id:05d}',purchase.purchased_at.date().isoformat(),purchase.supplier_name,purchase.invoice_number,
                    item.product.name,item.unit.name,_qty_text(item.quantity),_qty_text(item.base_quantity),
                    _money_text(item.cost_per_unit),_money_text(item.total_cost),purchase.created_by.get_full_name() or purchase.created_by.username,
                ])
        return headers,rows

    if kind=='expenses':
        qs=Expense.objects.filter(expense_date__range=(start,end)).select_related('created_by').order_by('expense_date','id')
        headers=['Date','Category','Description','Amount','Recorded by']
        rows=[[e.expense_date.isoformat(),e.get_category_display(),e.description,_money_text(e.amount),e.created_by.get_full_name() or e.created_by.username] for e in qs]
        return headers,rows

    if kind=='debts':
        customers=Customer.objects.prefetch_related('credit_sales','payments').order_by('name','id')
        headers=['Customer','Phone','Email','Credit sales','Payments','Outstanding balance','Active']
        rows=[[c.name,c.phone,c.email,_money_text(c.credit_sales_total),_money_text(c.payments_total),_money_text(c.current_balance),'Yes' if c.is_active else 'No'] for c in customers]
        return headers,rows

    if kind=='debt_payments':
        qs=CustomerPayment.objects.filter(paid_at__date__range=(start,end)).select_related('customer','received_by','voided_by').order_by('paid_at','id')
        headers=['Reference','Date','Time','Customer','Method','Amount','Status','Received by','External reference','Notes']
        rows=[[
            f'PAY-{p.id:05d}',p.paid_at.date().isoformat(),p.paid_at.strftime('%H:%M:%S'),p.customer.name,
            p.get_method_display(),_money_text(p.amount),'Voided' if p.is_voided else 'Active',
            p.received_by.get_full_name() or p.received_by.username,p.reference,p.notes,
        ] for p in qs]
        return headers,rows

    if kind=='closings':
        qs=CashClosing.objects.filter(close_date__range=(start,end)).select_related('cashier','closed_by','amended_by').order_by('close_date','id')
        headers=['Reference','Date','Cashier','Sales count','Debt payment count','Cash sales','Mobile sales','Bank sales','Debt sales','Debt cash collected','Debt mobile collected','Debt bank collected','Opening float','Cash paid out','Expected cash','Counted cash','Difference','Closed by','Closed at','Notes','Amended']
        rows=[[
            c.reference,c.close_date.isoformat(),c.cashier.get_full_name() or c.cashier.username,c.sale_count,c.debt_payment_count,
            _money_text(c.cash_sales),_money_text(c.mobile_money_sales),_money_text(c.bank_sales),_money_text(c.debt_sales),
            _money_text(c.debt_cash_collected),_money_text(c.debt_mobile_collected),_money_text(c.debt_bank_collected),
            _money_text(c.opening_float),_money_text(c.cash_paid_out),_money_text(c.expected_cash),_money_text(c.counted_cash),
            _money_text(c.difference),c.closed_by.get_full_name() or c.closed_by.username,c.closed_at.strftime('%Y-%m-%d %H:%M:%S'),
            c.notes,'Yes' if c.amended_at else 'No',
        ] for c in qs]
        return headers,rows

    if kind=='stocktakes':
        qs=Stocktake.objects.filter(count_date__range=(start,end)).select_related('created_by','posted_by').prefetch_related('lines').order_by('count_date','id')
        headers=['Reference','Date','Status','Products','Variance lines','Created by','Created at','Posted by','Posted at','Notes']
        rows=[[
            st.reference,st.count_date.isoformat(),st.get_status_display(),st.lines.count(),st.total_variance_lines,
            st.created_by.get_full_name() or st.created_by.username,st.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            (st.posted_by.get_full_name() or st.posted_by.username) if st.posted_by else '',
            st.posted_at.strftime('%Y-%m-%d %H:%M:%S') if st.posted_at else '',st.notes,
        ] for st in qs]
        return headers,rows

    raise ValueError('Unknown export type.')


def _ensure_default_unit(product):
    active_units = product.selling_units.filter(is_active=True)
    if active_units.exists() and not active_units.filter(is_default=True).exists():
        first = active_units.order_by('conversion_to_base', 'id').first()
        ProductUnit.objects.filter(pk=first.pk).update(is_default=True)


# -----------------------------------------------------------------------------
# Authentication / dashboard
# -----------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard' if _is_admin_user(request.user) else 'pos')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('dashboard' if _is_admin_user(user) else 'pos')
        messages.error(request, 'Wrong username or password.')
    return render(request, 'registration/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    if getattr(request.user.profile, 'role', 'cashier') == 'cashier' and not request.user.is_superuser:
        return redirect('pos')

    today = timezone.localdate()

    # ------------------------------------------------------------------
    # Dashboard period filter
    # Default is TODAY. Invalid dates safely fall back to today.
    # If From is after To, the dates are swapped automatically.
    # ------------------------------------------------------------------
    def parse_dashboard_date(value, fallback):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return fallback

    start_date = parse_dashboard_date(request.GET.get('start'), today)
    end_date = parse_dashboard_date(request.GET.get('end'), today)

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    period_days = (end_date - start_date).days + 1
    is_today_filter = start_date == today and end_date == today
    is_single_day = start_date == end_date

    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    month_start = today.replace(day=1)
    last_7_start = today - timedelta(days=6)

    if is_today_filter:
        period_label = 'Today'
        period_subtitle = today.strftime('%d %b %Y')
    elif is_single_day:
        period_label = start_date.strftime('%d %b %Y')
        period_subtitle = 'Single day'
    elif start_date.year == end_date.year:
        period_label = f"{start_date.strftime('%d %b')} – {end_date.strftime('%d %b %Y')}"
        period_subtitle = f'{period_days} days'
    else:
        period_label = f"{start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')}"
        period_subtitle = f'{period_days} days'

    # ------------------------------------------------------------------
    # Commercial totals for the selected period
    # ------------------------------------------------------------------
    sales = Sale.objects.filter(
        status='completed',
        sold_at__date__range=(start_date, end_date),
    )

    totals = sales.aggregate(
        revenue=Sum('grand_total'),
        profit=Sum('profit_total'),
        cogs=Sum('cogs_total'),
        item_discounts=Sum('item_discount_total'),
        bill_discounts=Sum('sale_discount'),
    )

    revenue = totals['revenue'] or Decimal('0')
    profit = totals['profit'] or Decimal('0')
    cogs_period = totals['cogs'] or Decimal('0')
    discounts_period = (
        (totals['item_discounts'] or Decimal('0')) +
        (totals['bill_discounts'] or Decimal('0'))
    )

    previous_totals = Sale.objects.filter(
        status='completed',
        sold_at__date__range=(previous_start, previous_end),
    ).aggregate(
        revenue=Sum('grand_total'),
        profit=Sum('profit_total'),
    )
    previous_revenue = previous_totals['revenue'] or Decimal('0')
    previous_profit = previous_totals['profit'] or Decimal('0')

    expenses_period = (
        Expense.objects
        .filter(expense_date__range=(start_date, end_date))
        .aggregate(v=Sum('amount'))['v']
        or Decimal('0')
    )

    purchases_period = (
        Purchase.objects
        .filter(purchased_at__date__range=(start_date, end_date))
        .aggregate(v=Sum('items__total_cost'))['v']
        or Decimal('0')
    )

    sale_count = sales.count()
    average_sale = (revenue / sale_count) if sale_count else Decimal('0')
    margin_pct = ((profit / revenue) * Decimal('100')) if revenue else Decimal('0')
    net_period = profit - expenses_period
    voided_period = Sale.objects.filter(
        status='voided',
        sold_at__date__range=(start_date, end_date),
    ).count()

    def comparison_text(current, previous):
        comparison_name = 'yesterday' if is_today_filter else 'previous period'
        if previous == 0:
            if current == 0:
                return f'No change from {comparison_name}'
            return f'No sales in {comparison_name}'
        change = ((current - previous) / abs(previous)) * Decimal('100')
        direction = 'up' if change >= 0 else 'down'
        return f'{abs(change):.1f}% {direction} from {comparison_name}'

    # ------------------------------------------------------------------
    # Current inventory position (not historical)
    # ------------------------------------------------------------------
    products = list(
        Product.objects.filter(is_active=True)
        .select_related('category')
        .prefetch_related('batches')
    )

    stock_value = Decimal('0')
    low_products = []
    out_of_stock_count = 0
    category_map = {}

    for product in products:
        batches = list(product.batches.all())
        stock_qty = sum(
            (b.quantity_remaining_base for b in batches),
            Decimal('0'),
        )
        product_value = sum(
            (
                b.quantity_remaining_base * b.unit_cost_base
                for b in batches
            ),
            Decimal('0'),
        )

        product.dashboard_stock_qty = stock_qty
        product.dashboard_stock_value = product_value
        stock_value += product_value

        if stock_qty <= 0:
            out_of_stock_count += 1

        if stock_qty <= product.low_stock_level:
            low_products.append(product)

        category_name = product.category.name
        category_row = category_map.setdefault(category_name, {
            'name': category_name,
            'value': Decimal('0'),
            'products': 0,
            'quantity_lines': 0,
        })
        category_row['value'] += product_value
        category_row['products'] += 1
        if stock_qty > 0:
            category_row['quantity_lines'] += 1

    low_products.sort(
        key=lambda p: (p.dashboard_stock_qty, p.name.lower())
    )

    category_stock = sorted(
        category_map.values(),
        key=lambda row: row['value'],
        reverse=True,
    )[:6]

    max_category_value = max(
        (row['value'] for row in category_stock),
        default=Decimal('0'),
    )

    for row in category_stock:
        row['percent'] = (
            float((row['value'] / max_category_value) * 100)
            if max_category_value else 0
        )

    # ------------------------------------------------------------------
    # Selected-period chart
    # ------------------------------------------------------------------
    sale_days = {
        row['day']: row
        for row in (
            Sale.objects
            .filter(
                status='completed',
                sold_at__date__range=(start_date, end_date),
            )
            .annotate(day=TruncDate('sold_at'))
            .values('day')
            .annotate(
                revenue=Sum('grand_total'),
                profit=Sum('profit_total'),
            )
        )
    }

    expense_days = {
        row['expense_date']: row['total'] or Decimal('0')
        for row in (
            Expense.objects
            .filter(expense_date__range=(start_date, end_date))
            .values('expense_date')
            .annotate(total=Sum('amount'))
        )
    }

    daily_chart = []
    for offset in range(period_days):
        day = start_date + timedelta(days=offset)
        sale_row = sale_days.get(day, {})
        daily_chart.append({
            'label': day.strftime('%d %b'),
            'revenue': float(sale_row.get('revenue') or 0),
            'profit': float(sale_row.get('profit') or 0),
            'expenses': float(expense_days.get(day) or 0),
        })

    # ------------------------------------------------------------------
    # Payment mix + top products for selected period
    # ------------------------------------------------------------------
    payment_rows = {
        row['payment_method']: row
        for row in sales.values('payment_method').annotate(
            amount=Sum('grand_total'),
            count=Count('id'),
        )
    }

    payment_summary = []
    for method, label in Sale.PAYMENT_CHOICES:
        row = payment_rows.get(method, {})
        payment_summary.append({
            'key': method,
            'label': label,
            'amount': row.get('amount') or Decimal('0'),
            'count': row.get('count') or 0,
        })

    top_products = list(
        SaleItem.objects.filter(
            sale__status='completed',
            sale__sold_at__date__range=(start_date, end_date),
        )
        .values('product__name', 'product__base_unit')
        .annotate(
            revenue=Sum('final_total'),
            quantity=Sum('base_quantity'),
        )
        .order_by('-revenue')[:5]
    )

    max_top_revenue = max(
        (
            row['revenue'] or Decimal('0')
            for row in top_products
        ),
        default=Decimal('0'),
    )

    for row in top_products:
        row['percent'] = (
            float(
                ((row['revenue'] or Decimal('0')) / max_top_revenue)
                * 100
            )
            if max_top_revenue else 0
        )

    dashboard_chart_data = {
        'daily': daily_chart,
        'payments': [
            {
                'key': row['key'],
                'label': row['label'],
                'value': float(row['amount']),
                'count': row['count'],
            }
            for row in payment_summary
        ],
    }

    recent_sales = (
        Sale.objects
        .filter(sold_at__date__range=(start_date, end_date))
        .select_related('cashier', 'customer')
        .order_by('-sold_at')[:8]
    )

    debt_customers=list(Customer.objects.filter(is_active=True).prefetch_related('credit_sales','payments'))
    outstanding_debt=sum((c.current_balance for c in debt_customers),Decimal('0'))
    debtor_count=sum(1 for c in debt_customers if c.current_balance>0)
    debt_sales_period=(
        sales.filter(payment_method='debt').aggregate(v=Sum('grand_total'))['v']
        or Decimal('0')
    )
    debt_collected_period=(
        CustomerPayment.objects.filter(
            is_voided=False,
            paid_at__date__range=(start_date,end_date),
        ).aggregate(v=Sum('amount'))['v']
        or Decimal('0')
    )

    return render(request, 'dashboard.html', {
        # Filter
        'filter_start': start_date.isoformat(),
        'filter_end': end_date.isoformat(),
        'today_iso': today.isoformat(),
        'last_7_start': last_7_start.isoformat(),
        'month_start': month_start.isoformat(),
        'period_label': period_label,
        'period_subtitle': period_subtitle,
        'period_days': period_days,
        'is_today_filter': is_today_filter,
        'is_last_7_filter': start_date == last_7_start and end_date == today,
        'is_month_filter': start_date == month_start and end_date == today,

        # Commercial metrics
        'today_sales': sale_count,
        'revenue': revenue,
        'gross_profit': profit,
        'expenses_today': expenses_period,
        'net_today': net_period,
        'average_sale': average_sale,
        'cogs_today': cogs_period,
        'discounts_today': discounts_period,
        'purchases_today': purchases_period,
        'margin_pct': margin_pct,
        'voided_today': voided_period,
        'revenue_compare': comparison_text(revenue, previous_revenue),
        'profit_compare': comparison_text(profit, previous_profit),

        # Current inventory
        'stock_value': stock_value,
        'active_products': len(products),
        'low_stock_count': len(low_products),
        'out_of_stock_count': out_of_stock_count,
        'outstanding_debt': outstanding_debt,
        'debtor_count': debtor_count,
        'debt_sales_period': debt_sales_period,
        'debt_collected_period': debt_collected_period,
        'low_products': low_products[:8],
        'category_stock': category_stock,

        # Detail data
        'payment_summary': payment_summary,
        'top_products': top_products,
        'recent_sales': recent_sales,
        'dashboard_chart_data': dashboard_chart_data,
    })


# -----------------------------------------------------------------------------
# POS / sales
# -----------------------------------------------------------------------------

@login_required
def pos(request):
    products = []
    for p in Product.objects.filter(is_active=True).select_related('category').prefetch_related('selling_units', 'batches'):
        units = [
            {
                'id': u.id, 'name': u.name, 'symbol': u.symbol,
                'conversion': str(u.conversion_to_base), 'price': str(u.selling_price),
                'default': u.is_default,
            }
            for u in p.selling_units.filter(is_active=True)
        ]
        if units:
            products.append({
                'id': p.id, 'name': p.name, 'category': p.category.name,
                'base_unit': p.base_unit, 'stock': str(p.stock_qty), 'units': units,
            })
    return render(request, 'pos.html', {'products_json': json.dumps(products)})


@login_required
@require_POST
def pos_checkout(request):
    try:
        cart = json.loads(request.POST.get('cart_json', '[]'))

        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip()

        sale = create_sale(
            cart=cart,
            payment_method=request.POST.get('payment_method', 'cash'),
            customer_name=customer_name,
            customer_phone=customer_phone,
            sale_discount=request.POST.get('sale_discount', '0'),
            cashier=request.user,
        )

        sold_at = timezone.localtime(sale.sold_at)

        receipt_items = []

        for item in sale.items.select_related('product', 'unit').all():
            receipt_items.append({
                'name': item.product.name,
                'unit': item.unit.symbol or item.unit.name,
                'quantity': str(item.quantity),
                'unit_price': str(item.normal_unit_price),
                'discount': str(item.discount_amount),
                'total': str(item.final_total),
            })

        receipt = {
            'sale_id': sale.id,
            'receipt_no': sale.receipt_no,

            'date': sold_at.strftime('%d/%m/%Y'),
            'time': sold_at.strftime('%H:%M:%S'),

            'cashier': (
                request.user.get_full_name().strip()
                or request.user.username
            ),

            'payment_method': sale.get_payment_method_display(),

            'customer_name': customer_name,
            'customer_phone': customer_phone,

            'items': receipt_items,

            'subtotal': str(sale.subtotal),
            'item_discount_total': str(sale.item_discount_total),
            'sale_discount': str(sale.sale_discount),
            'grand_total': str(sale.grand_total),
        }

        return JsonResponse({
            'ok': True,
            'sale_id': sale.id,
            'receipt_no': sale.receipt_no,
            'receipt': receipt,
        })

    except (
        StockError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        return JsonResponse({
            'ok': False,
            'error': str(exc),
        }, status=400)


@login_required
def sales(request):
    qs = Sale.objects.select_related('cashier','customer').all()
    if getattr(request.user.profile, 'role', 'cashier') == 'cashier' and not request.user.is_superuser:
        qs = qs.filter(cashier=request.user)
    return render(request, 'sales.html', {'sales': qs[:150]})


@login_required
def sale_detail(request, pk):
    sale = get_object_or_404(
        Sale.objects
        .select_related('cashier', 'customer')
        .prefetch_related('items__product', 'items__unit'),
        pk=pk,
    )

    if (
        getattr(request.user.profile, 'role', 'cashier') == 'cashier'
        and sale.cashier_id != request.user.id
        and not request.user.is_superuser
    ):
        return redirect('sales')

    return render(request, 'sale_detail.html', {
        'sale': sale,
        'qz_printer_name': getattr(settings, 'QZ_PRINTER_NAME', ''),
    })


@admin_required
@require_POST
def sale_void(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    try:
        void_sale(sale=sale, user=request.user, reason=request.POST.get('reason', ''))
        messages.success(request, 'Sale voided and stock restored.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('sale_detail', pk=pk)


# -----------------------------------------------------------------------------
# Products / categories / selling units
# -----------------------------------------------------------------------------

@admin_required
def products(request):
    q = request.GET.get('q', '').strip()
    qs = Product.objects.select_related('category').prefetch_related('selling_units', 'batches')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))
    return render(request, 'products.html', {
        'products': qs,
        'q': q,
        'categories': Category.objects.annotate(product_count=Count('products')).order_by('name'),
    })


@admin_required
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        product = form.save()
        messages.success(request, 'Product created. Now add at least one selling unit.')
        return redirect('product_edit', pk=product.pk)
    return render(request, 'product_form.html', {'form': form, 'title': 'Add Product'})

@admin_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)

    has_history = (
        product.batches.exists()
        or product.saleitem_set.exists()
        or product.purchaseitem_set.exists()
        or product.adjustments.exists()
    )

    # ---------------------------------------------------------
    # Product form
    # ---------------------------------------------------------

    form = ProductForm(
        request.POST or None,
        instance=product,
    )

    if has_history:
        form.fields['base_unit'].disabled = True
        form.fields['base_unit'].help_text = (
            'Base unit is locked because this product already '
            'has stock or transaction history.'
        )

    # ---------------------------------------------------------
    # Selling unit form
    # ---------------------------------------------------------

    unit_form = ProductUnitForm(
        prefix='unit',
        product=product,
    )

    # ---------------------------------------------------------
    # Update product
    # ---------------------------------------------------------

    if (
        request.method == 'POST'
        and request.POST.get('action') == 'product'
    ):
        if form.is_valid():
            form.save()

            messages.success(
                request,
                'Product updated successfully.'
            )

            return redirect(
                'product_edit',
                pk=product.pk,
            )

        messages.error(
            request,
            'Product was not saved. '
            'Check the highlighted field(s) and correct them.'
        )

    # ---------------------------------------------------------
    # Add selling unit
    # ---------------------------------------------------------

    if (
        request.method == 'POST'
        and request.POST.get('action') == 'unit'
    ):
        unit_form = ProductUnitForm(
            request.POST,
            prefix='unit',
            product=product,
        )

        if unit_form.is_valid():

            try:
                # Important:
                # all changes succeed together or roll back together.
                with transaction.atomic():

                    unit = unit_form.save(commit=False)
                    unit.product = product

                    if unit.is_default:
                        product.selling_units.update(
                            is_default=False
                        )

                    unit.save()

                    _ensure_default_unit(product)

            except IntegrityError:
                # Last-resort database protection.
                #
                # Normally clean_name() catches duplicates before
                # reaching here. This protects against unusual cases,
                # including two requests attempting the same save
                # at almost the same time.

                unit_name = (
                    unit_form.cleaned_data.get('name')
                    or 'This selling unit'
                )

                unit_form.add_error(
                    'name',
                    (
                        f'"{unit_name}" could not be added because '
                        f'this product already has a selling unit '
                        f'with that name. Edit the existing unit '
                        f'or choose a different name.'
                    )
                )

                messages.error(
                    request,
                    'Selling unit was not saved. '
                    'Check the highlighted field below.'
                )

            else:
                messages.success(
                    request,
                    'Selling unit added successfully.'
                )

                return redirect(
                    'product_edit',
                    pk=product.pk,
                )

        else:
            messages.error(
                request,
                'Selling unit was not saved. '
                'Check the highlighted field(s) below.'
            )

    return render(
        request,
        'product_form.html',
        {
            'form': form,
            'unit_form': unit_form,
            'product': product,
            'title': 'Edit Product',
            'base_unit_locked': has_history,
        }
    )



@admin_required
@require_POST
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    has_history = (
        product.batches.exists() or product.saleitem_set.exists() or
        product.purchaseitem_set.exists() or product.adjustments.exists()
    )
    if has_history:
        product.is_active = False
        product.save(update_fields=['is_active'])
        product.selling_units.update(is_active=False, is_default=False)
        messages.info(request, f'{product.name} has history, so it was archived instead of permanently deleted.')
    else:
        name = product.name
        product.delete()
        messages.success(request, f'{name} deleted.')
    return redirect('products')


@admin_required
@require_POST
def product_toggle(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active'])
    if not product.is_active:
        product.selling_units.update(is_active=False, is_default=False)
    messages.success(request, 'Product status updated.')
    return redirect('products')


@admin_required
@require_POST
def unit_update(request, pk):
    unit = get_object_or_404(ProductUnit, pk=pk)
    product = unit.product
    used_before = unit.saleitem_set.exists() or unit.purchaseitem_set.exists()

    unit.name = request.POST.get('name', unit.name).strip()
    unit.symbol = request.POST.get('symbol', unit.symbol).strip()
    requested_conversion = D(request.POST.get('conversion_to_base', unit.conversion_to_base))
    if requested_conversion <= 0:
        messages.error(request, 'Conversion must be greater than zero.')
        return redirect('product_edit', pk=product.pk)
    if used_before and requested_conversion != unit.conversion_to_base:
        messages.error(request, 'This unit has transaction history, so its conversion cannot be changed. Create a new unit instead.')
        return redirect('product_edit', pk=product.pk)

    unit.conversion_to_base = requested_conversion
    unit.selling_price = D(request.POST.get('selling_price', unit.selling_price))
    unit.is_default = request.POST.get('is_default') == 'on'
    unit.is_active = request.POST.get('is_active') == 'on'
    if unit.selling_price < 0:
        messages.error(request, 'Selling price cannot be negative.')
        return redirect('product_edit', pk=product.pk)
    if unit.is_default:
        product.selling_units.exclude(pk=unit.pk).update(is_default=False)
    if not unit.is_active:
        unit.is_default = False
    unit.save()
    _ensure_default_unit(product)
    messages.success(request, 'Selling unit updated.')
    return redirect('product_edit', pk=product.pk)


@admin_required
@require_POST
def unit_delete(request, pk):
    unit = get_object_or_404(ProductUnit, pk=pk)
    product = unit.product
    used_before = unit.saleitem_set.exists() or unit.purchaseitem_set.exists()
    if used_before:
        unit.is_active = False
        unit.is_default = False
        unit.save(update_fields=['is_active', 'is_default'])
        messages.info(request, f'{unit.name} has transaction history, so it was archived.')
    else:
        unit.delete()
        messages.success(request, 'Selling unit deleted.')
    _ensure_default_unit(product)
    return redirect('product_edit', pk=product.pk)


@admin_required
@require_POST
def category_create(request):
    form = CategoryForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, 'Category added.')
    else:
        messages.error(request, 'Category could not be added. Check the name and try again.')
    return redirect('products')


@admin_required
@require_POST
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST, instance=category)
    if form.is_valid():
        form.save()
        messages.success(request, 'Category updated.')
    else:
        messages.error(request, 'Category could not be updated.')
    return redirect('products')


@admin_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if category.products.exists():
        category.is_active = False
        category.save(update_fields=['is_active'])
        messages.info(request, f'{category.name} is used by products, so it was archived instead of deleted.')
    else:
        name = category.name
        category.delete()
        messages.success(request, f'{name} deleted.')
    return redirect('products')


# -----------------------------------------------------------------------------
# Customers / debts
# -----------------------------------------------------------------------------

@admin_required
def debts(request):
    q=request.GET.get('q','').strip()
    show=request.GET.get('show','debtors')

    qs=Customer.objects.all()
    if q:
        qs=qs.filter(
            Q(name__icontains=q) |
            Q(phone__icontains=q) |
            Q(email__icontains=q)
        )

    customers=list(qs.prefetch_related('credit_sales','payments'))
    for customer in customers:
        customer.dashboard_credit=customer.credit_sales_total
        customer.dashboard_paid=customer.payments_total
        customer.dashboard_balance=customer.current_balance

    if show!='all':
        customers=[c for c in customers if c.dashboard_balance>0]

    customers.sort(key=lambda c:(-c.dashboard_balance,c.name.lower()))

    all_customers=list(Customer.objects.filter(is_active=True).prefetch_related('credit_sales','payments'))
    total_outstanding=sum((c.current_balance for c in all_customers),Decimal('0'))
    debtor_count=sum(1 for c in all_customers if c.current_balance>0)
    total_credit_sales=sum((c.credit_sales_total for c in all_customers),Decimal('0'))

    month_start=timezone.localdate().replace(day=1)
    collected_month=(
        CustomerPayment.objects.filter(
            is_voided=False,
            paid_at__date__range=(month_start,timezone.localdate()),
        ).aggregate(v=Sum('amount'))['v']
        or Decimal('0')
    )

    return render(request,'debts.html',{
        'customers':customers[:250],
        'q':q,
        'show':show,
        'total_outstanding':total_outstanding,
        'debtor_count':debtor_count,
        'total_credit_sales':total_credit_sales,
        'collected_month':collected_month,
    })


@admin_required
def customer_detail(request,pk):
    customer=get_object_or_404(Customer,pk=pk)

    credit_sales=list(
        customer.credit_sales.filter(
            status='completed',
            payment_method='debt',
        ).select_related('cashier').order_by('sold_at','id')
    )
    active_payments=list(
        customer.payments.filter(is_voided=False)
        .select_related('received_by')
        .order_by('paid_at','id')
    )

    events=[]
    for sale in credit_sales:
        events.append({
            'when':sale.sold_at,
            'kind':'sale',
            'label':sale.receipt_no,
            'note':'Debt sale',
            'debit':sale.grand_total,
            'credit':Decimal('0'),
            'sale':sale,
        })
    for payment in active_payments:
        events.append({
            'when':payment.paid_at,
            'kind':'payment',
            'label':f'PAY-{payment.id:05d}',
            'note':payment.get_method_display(),
            'debit':Decimal('0'),
            'credit':payment.amount,
            'payment':payment,
        })

    events.sort(key=lambda row:(row['when'],row.get('kind','')))
    running=Decimal('0')
    for row in events:
        running += row['debit']-row['credit']
        row['balance']=running if running>0 else Decimal('0')
    statement=list(reversed(events))

    payment_history=customer.payments.select_related(
        'received_by','voided_by'
    ).order_by('-paid_at','-id')[:100]

    return render(request,'customer_detail.html',{
        'customer':customer,
        'credit_sales_total':customer.credit_sales_total,
        'payments_total':customer.payments_total,
        'balance':customer.current_balance,
        'credit_sales_count':len(credit_sales),
        'statement':statement,
        'payment_history':payment_history,
        'payment_methods':CustomerPayment.METHOD_CHOICES,
    })


@admin_required
def customer_edit(request,pk):
    customer=get_object_or_404(Customer,pk=pk)
    form=CustomerForm(request.POST or None,instance=customer)
    if request.method=='POST' and form.is_valid():
        form.save()
        messages.success(request,'Customer updated.')
        return redirect('customer_detail',pk=pk)
    return render(request,'customer_form.html',{
        'form':form,
        'customer':customer,
        'title':'Edit Customer',
    })


@admin_required
@require_POST
def customer_toggle(request,pk):
    customer=get_object_or_404(Customer,pk=pk)
    customer.is_active=not customer.is_active
    customer.save(update_fields=['is_active','updated_at'])
    messages.success(request,'Customer status updated.')
    return redirect('customer_detail',pk=pk)


@admin_required
@require_POST
def customer_payment_create(request,pk):
    customer=get_object_or_404(Customer,pk=pk)
    try:
        record_customer_payment(
            customer=customer,
            amount=request.POST.get('amount'),
            method=request.POST.get('method','cash'),
            reference=request.POST.get('reference',''),
            notes=request.POST.get('notes',''),
            user=request.user,
        )
        messages.success(request,'Debt payment recorded.')
    except StockError as exc:
        messages.error(request,str(exc))
    return redirect('customer_detail',pk=pk)


@admin_required
@require_POST
def customer_payment_void(request,pk):
    payment=get_object_or_404(CustomerPayment,pk=pk)
    customer_id=payment.customer_id
    try:
        void_customer_payment(
            payment=payment,
            user=request.user,
            reason=request.POST.get('reason',''),
        )
        messages.success(request,'Customer payment voided. The debt balance has been restored.')
    except StockError as exc:
        messages.error(request,str(exc))
    return redirect('customer_detail',pk=customer_id)

# -----------------------------------------------------------------------------
# Stock / FIFO
# -----------------------------------------------------------------------------

@admin_required
def stock(request):
    q = request.GET.get('q', '').strip()

    products_qs = (
        Product.objects
        .select_related('category')
        .prefetch_related('batches')
        .order_by('name')
    )

    if q:
        products_qs = products_qs.filter(
            Q(name__icontains=q)
            | Q(category__name__icontains=q)
        )

    products_qs = list(products_qs)

    for product in products_qs:
        product_batches = list(
            product.batches.all()
        )

        current_qty = sum(
            (
                batch.quantity_remaining_base
                for batch in product_batches
            ),
            Decimal('0'),
        )

        current_value = sum(
            (
                batch.quantity_remaining_base
                * batch.unit_cost_base
                for batch in product_batches
            ),
            Decimal('0'),
        )

        if current_qty > 0:
            average_cost = (
                current_value / current_qty
            )
        else:
            average_cost = Decimal('0')

        product.current_stock_qty = current_qty
        product.current_stock_value = current_value
        product.average_buying_cost = average_cost

        product.current_is_low_stock = (
            current_qty
            <= product.low_stock_level
        )

    batch_qs = (
        StockBatch.objects
        .select_related(
            'product',
            'purchase_item',
            'purchase_item__unit',
            'purchase_item__purchase',
        )
        .prefetch_related(
            'sale_allocations'
        )
        .filter(
            quantity_remaining_base__gt=0
        )
    )

    if q:
        batch_qs = batch_qs.filter(
            Q(product__name__icontains=q)
            | Q(product__category__name__icontains=q)
        )

    active_batches = list(
        batch_qs.order_by(
            'received_at',
            'id',
        )[:100]
    )

    for batch in active_batches:
        allocations = list(
            batch.sale_allocations.all()
        )

        batch.can_edit_cost = (
            batch.quantity_remaining_base
            == batch.quantity_received_base
            and not allocations
        )

        batch.remaining_value = (
            batch.quantity_remaining_base
            * batch.unit_cost_base
        )

        if batch.purchase_item_id:
            item = batch.purchase_item

            batch.display_buying_price = (
                item.cost_per_unit
            )

            batch.display_buying_unit = (
                item.unit.symbol
            )

        else:
            batch.display_buying_price = (
                batch.unit_cost_base
            )

            batch.display_buying_unit = (
                batch.product.base_unit
            )

    adjustments_qs = (
        StockAdjustment.objects
        .select_related(
            'product',
            'created_by',
        )
    )

    if q:
        adjustments_qs = (
            adjustments_qs.filter(
                Q(product__name__icontains=q)
                | Q(
                    product__category__name__icontains=q
                )
            )
        )

    return render(
        request,
        'stock.html',
        {
            'products': products_qs,
            'adjustments': adjustments_qs[:10],
            'batches': active_batches,
            'q': q,
        },
    )


@admin_required
def stock_detail(request, pk):
    """
    Detailed stock page for one product.

    Displays all FIFO batches including fully used batches,
    buying prices, remaining quantities and values.
    """

    product = get_object_or_404(
        Product.objects
        .select_related('category')
        .prefetch_related('selling_units'),
        pk=pk,
    )

    batches = list(
        StockBatch.objects
        .filter(product=product)
        .select_related(
            'purchase_item',
            'purchase_item__unit',
            'purchase_item__purchase',
            'created_by',
        )
        .prefetch_related('sale_allocations')
        .order_by('-received_at', '-id')
    )

    current_qty = Decimal('0')
    current_value = Decimal('0')

    for batch in batches:
        allocations = list(batch.sale_allocations.all())

        batch.used_quantity = (
            batch.quantity_received_base
            - batch.quantity_remaining_base
        )

        batch.remaining_value = (
            batch.quantity_remaining_base
            * batch.unit_cost_base
        )

        batch.original_value = (
            batch.quantity_received_base
            * batch.unit_cost_base
        )

        current_qty += batch.quantity_remaining_base
        current_value += batch.remaining_value

        batch.can_edit_cost = (
            batch.quantity_remaining_base
            == batch.quantity_received_base
            and not allocations
        )

        batch.has_history = bool(
            allocations
            or (
                batch.quantity_remaining_base
                != batch.quantity_received_base
            )
        )

        if batch.purchase_item_id:
            item = batch.purchase_item

            batch.display_buying_price = (
                item.cost_per_unit
            )
            batch.display_buying_unit = (
                item.unit.symbol
            )

            batch.received_display_qty = (
                item.quantity
            )
            batch.received_display_unit = (
                item.unit.symbol
            )

            batch.purchase_reference = (
                f'PUR-{item.purchase_id:05d}'
            )

        else:
            batch.display_buying_price = (
                batch.unit_cost_base
            )
            batch.display_buying_unit = (
                product.base_unit
            )

            batch.received_display_qty = (
                batch.quantity_received_base
            )
            batch.received_display_unit = (
                product.base_unit
            )

            batch.purchase_reference = '-'

    average_buying_cost = Decimal('0')

    if current_qty > 0:
        average_buying_cost = (
            current_value / current_qty
        )

    selling_units = (
        product.selling_units
        .filter(is_active=True)
        .order_by('conversion_to_base')
    )

    return render(
        request,
        'stock_detail.html',
        {
            'product': product,
            'batches': batches,
            'selling_units': selling_units,
            'current_qty': current_qty,
            'current_value': current_value,
            'average_buying_cost': average_buying_cost,
        },
    )


@admin_required
@transaction.atomic
def stock_batch_cost_edit(request, pk):
    """
    Correct buying price for an UNTOUCHED FIFO batch.

    Purchase batch:
        Updates both PurchaseItem and StockBatch.

    Opening/manual batch:
        Updates StockBatch directly.

    A batch that has already been sold/consumed is locked because
    historical SaleAllocation cost and profit already depend on it.
    """

    batch = get_object_or_404(
        StockBatch.objects
        .select_for_update()
        .select_related(
            'product',
            'purchase_item',
            'purchase_item__unit',
            'purchase_item__purchase',
        ),
        pk=pk,
    )

    product = batch.product

    has_allocations = (
        batch.sale_allocations.exists()
    )

    untouched = (
        batch.quantity_remaining_base
        == batch.quantity_received_base
        and not has_allocations
    )

    if not untouched:
        messages.error(
            request,
            (
                'Buying price cannot be changed because '
                'stock from this batch has already been '
                'used or sold. Historical FIFO cost and '
                'profit are protected.'
            ),
        )

        return redirect(
            'stock_detail',
            pk=product.pk,
        )

    if batch.purchase_item_id:
        item = batch.purchase_item

        current_cost = item.cost_per_unit
        cost_unit = item.unit.symbol
        cost_label = (
            f'Buying price per {item.unit.symbol}'
        )
    else:
        item = None

        current_cost = batch.unit_cost_base
        cost_unit = product.base_unit
        cost_label = (
            f'Buying price per {product.base_unit}'
        )

    if request.method == 'POST':
        raw_cost = (
            request.POST
            .get('cost_per_unit', '')
            .strip()
        )

        if not raw_cost:
            messages.error(
                request,
                'Enter the correct buying price.',
            )
        else:
            try:
                new_cost = Decimal(
                    raw_cost.replace(',', '')
                )

                if new_cost < 0:
                    raise ValueError

            except (
                InvalidOperation,
                ValueError,
            ):
                messages.error(
                    request,
                    (
                        'Enter a valid buying price '
                        'greater than or equal to zero.'
                    ),
                )

            else:
                if item is not None:
                    # Purchase buying price is entered
                    # per selected purchase unit.
                    total_cost = (
                        item.quantity * new_cost
                    ).quantize(
                        Decimal('0.01')
                    )

                    if item.base_quantity > 0:
                        unit_cost_base = (
                            total_cost
                            / item.base_quantity
                        )
                    else:
                        unit_cost_base = Decimal('0')

                    item.cost_per_unit = new_cost
                    item.total_cost = total_cost
                    item.unit_cost_base = unit_cost_base

                    item.save(
                        update_fields=[
                            'cost_per_unit',
                            'total_cost',
                            'unit_cost_base',
                        ]
                    )

                    batch.unit_cost_base = (
                        unit_cost_base
                    )

                    batch.save(
                        update_fields=[
                            'unit_cost_base',
                        ]
                    )

                else:
                    # Opening stock / manual increase:
                    # price is already cost per base unit.
                    batch.unit_cost_base = new_cost

                    batch.save(
                        update_fields=[
                            'unit_cost_base',
                        ]
                    )

                messages.success(
                    request,
                    (
                        f'Buying price for '
                        f'{product.name} was corrected '
                        f'successfully.'
                    ),
                )

                return redirect(
                    'stock_detail',
                    pk=product.pk,
                )

    return render(
        request,
        'stock_cost_edit.html',
        {
            'batch': batch,
            'product': product,
            'current_cost': current_cost,
            'cost_unit': cost_unit,
            'cost_label': cost_label,
        },
    )


@admin_required
def opening_stock(request):
    products_qs = Product.objects.filter(
        is_active=True
    ).prefetch_related(
        'selling_units'
    )

    if request.method == 'POST':
        product = get_object_or_404(
            Product,
            pk=request.POST.get('product'),
        )

        unit = get_object_or_404(
            ProductUnit,
            pk=request.POST.get('unit'),
            product=product,
        )

        try:
            qty = D(
                request.POST.get('quantity')
            )

            cost_per_unit = D(
                request.POST.get(
                    'cost_per_unit',
                    '0',
                )
            )

            if (
                qty <= 0
                or cost_per_unit < 0
            ):
                raise StockError(
                    (
                        'Enter a valid opening '
                        'quantity and cost.'
                    )
                )

            base_qty = (
                qty
                * unit.conversion_to_base
            )

            cost_base = (
                cost_per_unit
                / unit.conversion_to_base
            ) if unit.conversion_to_base else D('0')

            add_opening_stock(
                product=product,
                quantity_base=base_qty,
                unit_cost_base=cost_base,
                reference=request.POST.get(
                    'reference',
                    '',
                ),
                user=request.user,
            )

            messages.success(
                request,
                (
                    f'Opening stock added for '
                    f'{product.name}.'
                ),
            )

            return redirect('stock')

        except StockError as exc:
            messages.error(
                request,
                str(exc),
            )

    data = {
        str(p.id): [
            {
                'id': u.id,
                'name': u.name,
                'symbol': u.symbol,
                'conversion': str(
                    u.conversion_to_base
                ),
            }
            for u in p.selling_units.filter(
                is_active=True
            )
        ]
        for p in products_qs
    }

    return render(
        request,
        'opening_stock.html',
        {
            'products': products_qs,
            'units_json': json.dumps(data),
        },
    )


@admin_required
@require_POST
def opening_batch_delete(request, pk):
    batch = get_object_or_404(
        StockBatch,
        pk=pk,
        source='OPENING',
        purchase_item__isnull=True,
    )

    if (
        batch.reference
        or ''
    ).startswith('Adjustment:'):
        messages.error(
            request,
            (
                'Adjustment stock cannot be deleted '
                'here. Create a correcting stock '
                'adjustment instead.'
            ),
        )

    elif (
        batch.sale_allocations.exists()
        or (
            batch.quantity_remaining_base
            != batch.quantity_received_base
        )
    ):
        messages.error(
            request,
            (
                'This opening batch has already been '
                'used, so it cannot be deleted. '
                'Use a stock adjustment instead.'
            ),
        )

    else:
        product_name = batch.product.name
        batch.delete()

        messages.success(
            request,
            (
                f'Unused opening stock for '
                f'{product_name} was removed.'
            ),
        )

    return redirect('stock')


@admin_required
def stock_adjust(request):
    if request.method == 'POST':
        product = get_object_or_404(
            Product,
            pk=request.POST.get('product'),
        )

        adjustment_type = request.POST.get(
            'adjustment_type'
        )

        reason = request.POST.get(
            'reason',
            'Stock adjustment',
        ).strip()

        try:
            if adjustment_type == 'decrease':
                decrease_stock_fifo(
                    product=product,
                    quantity=request.POST.get(
                        'quantity'
                    ),
                    user=request.user,
                    reason=reason,
                )

            else:
                increase_stock(
                    product=product,
                    quantity=request.POST.get(
                        'quantity'
                    ),
                    unit_cost_base=request.POST.get(
                        'unit_cost',
                        '0',
                    ),
                    user=request.user,
                    reason=reason,
                )

            messages.success(
                request,
                'Stock adjustment saved.',
            )

            return redirect('stock')

        except StockError as exc:
            messages.error(
                request,
                str(exc),
            )

    return render(
        request,
        'stock_adjust.html',
        {
            'products': Product.objects.filter(
                is_active=True
            ),
        },
    )


@admin_required
def opening_stock(request):
    products_qs = Product.objects.filter(is_active=True).prefetch_related('selling_units')
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=request.POST.get('product'))
        unit = get_object_or_404(ProductUnit, pk=request.POST.get('unit'), product=product)
        try:
            qty = D(request.POST.get('quantity'))
            cost_per_unit = D(request.POST.get('cost_per_unit', '0'))
            if qty <= 0 or cost_per_unit < 0:
                raise StockError('Enter a valid opening quantity and cost.')
            base_qty = qty * unit.conversion_to_base
            cost_base = (cost_per_unit / unit.conversion_to_base) if unit.conversion_to_base else D('0')
            add_opening_stock(
                product=product, quantity_base=base_qty, unit_cost_base=cost_base,
                reference=request.POST.get('reference', ''), user=request.user,
            )
            messages.success(request, f'Opening stock added for {product.name}.')
            return redirect('stock')
        except StockError as exc:
            messages.error(request, str(exc))
    data = {
        str(p.id): [
            {'id': u.id, 'name': u.name, 'symbol': u.symbol, 'conversion': str(u.conversion_to_base)}
            for u in p.selling_units.filter(is_active=True)
        ]
        for p in products_qs
    }
    return render(request, 'opening_stock.html', {'products': products_qs, 'units_json': json.dumps(data)})


@admin_required
@require_POST
def opening_batch_delete(request, pk):
    batch = get_object_or_404(StockBatch, pk=pk, source='OPENING', purchase_item__isnull=True)
    if (batch.reference or '').startswith('Adjustment:'):
        messages.error(request, 'Adjustment stock cannot be deleted here. Create a correcting stock adjustment instead.')
    elif batch.sale_allocations.exists() or batch.quantity_remaining_base != batch.quantity_received_base:
        messages.error(request, 'This opening batch has already been used, so it cannot be deleted. Use a stock adjustment instead.')
    else:
        product_name = batch.product.name
        batch.delete()
        messages.success(request, f'Unused opening stock for {product_name} was removed.')
    return redirect('stock')


@admin_required
def stock_adjust(request):
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=request.POST.get('product'))
        adjustment_type = request.POST.get('adjustment_type')
        reason = request.POST.get('reason', 'Stock adjustment').strip()
        try:
            if adjustment_type == 'decrease':
                decrease_stock_fifo(product=product, quantity=request.POST.get('quantity'), user=request.user, reason=reason)
            else:
                increase_stock(
                    product=product, quantity=request.POST.get('quantity'),
                    unit_cost_base=request.POST.get('unit_cost', '0'),
                    user=request.user, reason=reason,
                )
            messages.success(request, 'Stock adjustment saved.')
            return redirect('stock')
        except StockError as exc:
            messages.error(request, str(exc))
    return render(request, 'stock_adjust.html', {'products': Product.objects.filter(is_active=True)})


# -----------------------------------------------------------------------------
# Purchases
# -----------------------------------------------------------------------------

@admin_required
def purchases(request):
    return render(request, 'purchases.html', {
        'purchases': Purchase.objects.select_related('created_by').prefetch_related(
            'items__product', 'items__unit', 'items__batch'
        )[:100]
    })


@admin_required
def purchase_create(request):
    products_qs = Product.objects.filter(is_active=True).prefetch_related('selling_units')
    if request.method == 'POST':
        product = get_object_or_404(Product, pk=request.POST.get('product'))
        unit = get_object_or_404(ProductUnit, pk=request.POST.get('unit'), product=product)
        try:
            add_purchase(
                product=product, unit=unit, quantity=request.POST.get('quantity'),
                cost_per_unit=request.POST.get('cost_per_unit'),
                supplier_name=request.POST.get('supplier_name', ''),
                invoice_number=request.POST.get('invoice_number', ''),
                notes=request.POST.get('notes', ''), user=request.user,
            )
            messages.success(request, 'Purchase received and stock batch created.')
            return redirect('purchases')
        except StockError as exc:
            messages.error(request, str(exc))
    data = {
        str(p.id): [
            {'id': u.id, 'name': u.name, 'symbol': u.symbol, 'conversion': str(u.conversion_to_base)}
            for u in p.selling_units.filter(is_active=True)
        ]
        for p in products_qs
    }
    return render(request, 'purchase_form.html', {'products': products_qs, 'units_json': json.dumps(data)})


@admin_required
def purchase_edit(request, pk):
    purchase = get_object_or_404(Purchase.objects.prefetch_related('items__batch'), pk=pk)
    form = PurchaseMetaForm(request.POST or None, instance=purchase)
    if request.method == 'POST' and form.is_valid():
        old_invoice = purchase.invoice_number
        purchase = form.save()
        new_reference = purchase.invoice_number or f'PUR-{purchase.id:05d}'
        for item in purchase.items.select_related('batch'):
            if hasattr(item, 'batch'):
                item.batch.reference = new_reference
                item.batch.save(update_fields=['reference'])
        messages.success(request, 'Purchase details updated. Stock quantity and cost were left unchanged.')
        return redirect('purchases')
    return render(request, 'purchase_edit.html', {'purchase': purchase, 'form': form})


@admin_required
@require_POST
def purchase_delete(request, pk):
    purchase = get_object_or_404(Purchase.objects.prefetch_related('items__batch'), pk=pk)
    batches = []
    safe = True
    for item in purchase.items.all():
        try:
            batch = item.batch
        except StockBatch.DoesNotExist:
            batch = None
        if batch:
            batches.append(batch)
            if batch.quantity_remaining_base != batch.quantity_received_base or batch.sale_allocations.exists():
                safe = False
                break
    if not safe:
        messages.error(request, 'This purchase cannot be deleted because some of its stock has already been sold or adjusted.')
        return redirect('purchases')
    ref = f'PUR-{purchase.id:05d}'
    purchase.delete()
    messages.success(request, f'{ref} deleted and its untouched stock removed.')
    return redirect('purchases')


# -----------------------------------------------------------------------------
# Expenses
# -----------------------------------------------------------------------------

@login_required
def expenses(request):
    form = ExpenseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, 'Expense saved.')
        return redirect('expenses')
    return render(request, 'expenses.html', {
        'form': form,
        'expenses': Expense.objects.select_related('created_by')[:100],
    })


@login_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Expense updated.')
        return redirect('expenses')
    return render(request, 'expense_form.html', {'form': form, 'expense': expense})


@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    description = expense.description
    expense.delete()
    messages.success(request, f'Expense “{description}” deleted.')
    return redirect('expenses')


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------

@admin_required
def reports(request):
    today = timezone.localdate()
    start = request.GET.get('start') or str(today.replace(day=1))
    end = request.GET.get('end') or str(today)
    sale_qs = Sale.objects.filter(status='completed', sold_at__date__range=[start, end])
    revenue = sale_qs.aggregate(v=Sum('grand_total'))['v'] or Decimal('0')
    cogs = sale_qs.aggregate(v=Sum('cogs_total'))['v'] or Decimal('0')
    gross = sale_qs.aggregate(v=Sum('profit_total'))['v'] or Decimal('0')
    expense_total = Expense.objects.filter(expense_date__range=[start, end]).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    discounts = (
        (sale_qs.aggregate(v=Sum('item_discount_total'))['v'] or Decimal('0')) +
        (sale_qs.aggregate(v=Sum('sale_discount'))['v'] or Decimal('0'))
    )
    debt_sales = sale_qs.filter(payment_method='debt').aggregate(v=Sum('grand_total'))['v'] or Decimal('0')
    debt_collected = CustomerPayment.objects.filter(
        is_voided=False,
        paid_at__date__range=[start,end],
    ).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    active_customers=list(Customer.objects.filter(is_active=True).prefetch_related('credit_sales','payments'))
    outstanding_debt=sum((c.current_balance for c in active_customers),Decimal('0'))

    top_items = sale_qs.values('items__product__name').annotate(
        qty=Sum('items__base_quantity'), amount=Sum('items__final_total')
    ).order_by('-amount')[:10]
    return render(request, 'reports.html', {
        'start': start, 'end': end, 'revenue': revenue, 'cogs': cogs,
        'gross': gross, 'expenses_total': expense_total, 'net': gross - expense_total,
        'discounts': discounts, 'sales_count': sale_qs.count(), 'top_items': top_items,
        'debt_sales': debt_sales, 'debt_collected': debt_collected,
        'outstanding_debt': outstanding_debt,
    })



# -----------------------------------------------------------------------------
# End Day / cash reconciliation
# -----------------------------------------------------------------------------

@login_required
def end_day(request):
    today = timezone.localdate()

    selected_date = _parse_date(
        request.GET.get('date'),
        today,
    )

    if selected_date > today:
        selected_date = today

    is_admin = _is_admin_user(request.user)

    # -------------------------------------------------------------------------
    # AVAILABLE CASHIERS
    # Superusers are intentionally hidden from the End Day cashier dropdown.
    # This does NOT affect their permissions or ability to access this page.
    # -------------------------------------------------------------------------
    if is_admin:
        available_users = (
            User.objects
            .filter(
                is_active=True,
                is_superuser=False,
            )
            .select_related('profile')
            .order_by(
                'first_name',
                'username',
            )
        )
    else:
        available_users = (
            User.objects
            .filter(pk=request.user.pk)
            .select_related('profile')
        )

    # -------------------------------------------------------------------------
    # SELECTED CASHIER
    # -------------------------------------------------------------------------
    requested_user_id = (
        request.GET.get('cashier')
        if is_admin
        else str(request.user.pk)
    )

    try:
        if requested_user_id:
            target = available_users.get(
                pk=int(requested_user_id)
            )
        else:
            # Superuser/admin can still access the page normally.
            # Prefer first visible cashier when no cashier is explicitly chosen.
            target = (
                available_users.first()
                if is_admin
                else request.user
            )

            # Safety fallback if there are no normal active users yet.
            if target is None:
                target = request.user

    except (
        ValueError,
        TypeError,
        User.DoesNotExist,
    ):
        target = (
            available_users.first()
            if is_admin
            else request.user
        )

        if target is None:
            target = request.user

    # -------------------------------------------------------------------------
    # SAVE END DAY
    # -------------------------------------------------------------------------
    if request.method == 'POST':
        selected_date = _parse_date(
            request.POST.get('close_date'),
            today,
        )

        if selected_date > today:
            selected_date = today

        target = request.user

        if is_admin:
            try:
                cashier_id = int(
                    request.POST.get('cashier_id') or 0
                )

                # Use available_users instead of User.objects.get().
                # This prevents a hidden superuser from being selected manually
                # through a modified POST request.
                target = available_users.get(
                    pk=cashier_id
                )

            except (
                ValueError,
                TypeError,
                User.DoesNotExist,
            ):
                # If the logged-in account itself is not a visible cashier
                # (for example, a superuser), fall back to the first normal user.
                target = available_users.first()

                if target is None:
                    target = request.user

        try:
            closing = close_cash_day(
                cashier=target,
                close_date=selected_date,
                opening_float=request.POST.get(
                    'opening_float',
                    '0',
                ),
                cash_paid_out=request.POST.get(
                    'cash_paid_out',
                    '0',
                ),
                counted_cash=request.POST.get(
                    'counted_cash',
                    '0',
                ),
                notes=request.POST.get(
                    'notes',
                    '',
                ),
                user=request.user,
            )

            messages.success(
                request,
                (
                    f'{closing.reference} saved. '
                    f'Difference: '
                    f'{_money_text(closing.difference)} TZS.'
                ),
            )

            return redirect(
                f"{request.path}"
                f"?date={selected_date.isoformat()}"
                f"&cashier={target.id}"
            )

        except StockError as exc:
            messages.error(
                request,
                str(exc),
            )

    # -------------------------------------------------------------------------
    # LIVE CASH CLOSING SNAPSHOT
    # -------------------------------------------------------------------------
    live = calculate_cash_closing_snapshot(
        cashier=target,
        close_date=selected_date,
    )

    live['sales_total'] = (
        live['cash_sales']
        + live['mobile_money_sales']
        + live['bank_sales']
        + live['debt_sales']
    )

    live['debt_collected_total'] = (
        live['debt_cash_collected']
        + live['debt_mobile_collected']
        + live['debt_bank_collected']
    )

    base_cash = (
        live['cash_sales']
        + live['debt_cash_collected']
    )

    # -------------------------------------------------------------------------
    # EXISTING CLOSING
    # -------------------------------------------------------------------------
    existing = (
        CashClosing.objects
        .filter(
            cashier=target,
            close_date=selected_date,
        )
        .select_related(
            'closed_by',
            'amended_by',
        )
        .first()
    )

    summary = dict(live)

    if existing:
        summary.update({
            'cash_sales':
                existing.cash_sales,

            'mobile_money_sales':
                existing.mobile_money_sales,

            'bank_sales':
                existing.bank_sales,

            'debt_sales':
                existing.debt_sales,

            'debt_cash_collected':
                existing.debt_cash_collected,

            'debt_mobile_collected':
                existing.debt_mobile_collected,

            'debt_bank_collected':
                existing.debt_bank_collected,

            'sale_count':
                existing.sale_count,

            'payment_count':
                existing.debt_payment_count,

            'sales_total':
                existing.sales_total,

            'debt_collected_total':
                existing.debt_collected_total,
        })

    # -------------------------------------------------------------------------
    # CLOSING HISTORY
    # -------------------------------------------------------------------------
    history = (
        CashClosing.objects
        .select_related(
            'cashier',
            'closed_by',
            'amended_by',
        )
    )

    if not is_admin:
        history = history.filter(
            cashier=request.user
        )

    # -------------------------------------------------------------------------
    # RENDER
    # -------------------------------------------------------------------------
    return render(
        request,
        'end_day.html',
        {
            'selected_date':
                selected_date,

            'today':
                today,

            'target_cashier':
                target,

            'available_users':
                available_users,

            'is_admin_view':
                is_admin,

            'live':
                live,

            'summary':
                summary,

            'base_cash':
                base_cash,

            'existing':
                existing,

            'history':
                history[:40],
        },
    )

@admin_required
@require_POST
def cash_closing_amend(request,pk):
    closing=get_object_or_404(CashClosing,pk=pk)
    try:
        amend_cash_closing(
            closing=closing,
            opening_float=request.POST.get('opening_float','0'),
            cash_paid_out=request.POST.get('cash_paid_out','0'),
            counted_cash=request.POST.get('counted_cash','0'),
            notes=request.POST.get('notes',''),
            amendment_reason=request.POST.get('amendment_reason',''),
            user=request.user,
        )
        messages.success(request,'Closing amended. The original sales/collection snapshot was preserved.')
    except StockError as exc:
        messages.error(request,str(exc))
    return redirect(f"{reverse('end_day')}?date={closing.close_date.isoformat()}&cashier={closing.cashier_id}")


# -----------------------------------------------------------------------------
# Stocktake
# -----------------------------------------------------------------------------

@login_required
def stocktakes(request):
    if request.method=='POST':
        count_date=_parse_date(request.POST.get('count_date'),timezone.localdate())
        try:
            stocktake=create_stocktake(count_date=count_date,notes=request.POST.get('notes',''),user=request.user)
            messages.success(request,f'{stocktake.reference} created. Enter the physical counts, then post it.')
            return redirect('stocktake_detail',pk=stocktake.pk)
        except StockError as exc:
            messages.error(request,str(exc))
    sessions=Stocktake.objects.select_related('created_by','posted_by','cancelled_by').prefetch_related('lines')[:100]
    return render(request,'stocktakes.html',{'stocktakes':sessions,'today':timezone.localdate()})


def _save_stocktake_counts_from_post(stocktake,post):
    if stocktake.status!='draft':
        raise StockError('Only a draft stocktake can be edited.')
    stocktake.notes=(post.get('notes') or '').strip()
    stocktake.save(update_fields=['notes'])
    for line in stocktake.lines.select_related('product').order_by('id'):
        raw=(post.get(f'counted_{line.id}') or '').strip()
        unit_value=(post.get(f'unit_{line.id}') or 'base').strip()
        if raw=='':
            line.counted_input_qty=None
            line.counted_qty_base=None
            line.difference_base=Decimal('0')
            line.count_unit=None
            line.count_unit_name=line.product.base_unit
            line.count_unit_symbol=line.product.base_unit
            line.count_conversion=Decimal('1')
            line.save(update_fields=['counted_input_qty','counted_qty_base','difference_base','count_unit','count_unit_name','count_unit_symbol','count_conversion'])
            continue
        qty=D(raw)
        if qty<0:
            raise StockError(f'Physical count for {line.product.name} cannot be negative.')
        unit=None
        conversion=Decimal('1')
        unit_name=line.product.base_unit
        unit_symbol=line.product.base_unit
        if unit_value!='base':
            try:
                unit=ProductUnit.objects.get(pk=int(unit_value),product=line.product,is_active=True)
            except (ValueError,TypeError,ProductUnit.DoesNotExist):
                raise StockError(f'Choose a valid count unit for {line.product.name}.')
            conversion=unit.conversion_to_base
            unit_name=unit.name
            unit_symbol=unit.symbol
        counted_base=qty*conversion
        line.counted_input_qty=qty
        line.count_unit=unit
        line.count_unit_name=unit_name
        line.count_unit_symbol=unit_symbol
        line.count_conversion=conversion
        line.counted_qty_base=counted_base
        line.difference_base=counted_base-line.system_qty_base
        line.save(update_fields=['counted_input_qty','count_unit','count_unit_name','count_unit_symbol','count_conversion','counted_qty_base','difference_base'])


@login_required
def stocktake_detail(request,pk):
    stocktake=get_object_or_404(Stocktake.objects.select_related('created_by','posted_by','cancelled_by'),pk=pk)
    if request.method=='POST':
        action=request.POST.get('action','save')
        try:
            if action in ('save','post'):
                _save_stocktake_counts_from_post(stocktake,request.POST)
                stocktake.refresh_from_db()
                if action=='post':
                    post_stocktake(stocktake=stocktake,user=request.user)
                    messages.success(request,f'{stocktake.reference} posted. Stock adjustments were created from the physical differences.')
                else:
                    messages.success(request,'Stocktake draft saved.')
            elif action=='refresh':
                _save_stocktake_counts_from_post(stocktake,request.POST)
                refresh_stocktake_snapshot(stocktake=stocktake)
                messages.success(request,'Draft saved and system quantities refreshed. Review the differences before posting.')
            elif action=='cancel':
                cancel_stocktake(stocktake=stocktake,user=request.user)
                messages.success(request,'Stocktake cancelled. No stock was changed.')
            return redirect('stocktake_detail',pk=pk)
        except StockError as exc:
            messages.error(request,str(exc))
            stocktake.refresh_from_db()

    lines=list(stocktake.lines.select_related('product','count_unit','adjustment').prefetch_related('product__selling_units').order_by('product__name'))
    for line in lines:
        line.unit_options=[
            {'value':'base','label':f'Base ({line.product.base_unit})','conversion':Decimal('1')},
            *[
                {'value':str(unit.id),'label':f'{unit.name} ({unit.symbol})','conversion':unit.conversion_to_base}
                for unit in line.product.selling_units.filter(is_active=True).order_by('conversion_to_base','id')
                if not (unit.conversion_to_base==1 and unit.symbol.upper()==line.product.base_unit.upper())
            ],
        ]
        line.selected_unit_value=str(line.count_unit_id) if line.count_unit_id else 'base'
    counted=sum(1 for line in lines if line.counted_qty_base is not None)
    variance=sum(1 for line in lines if line.counted_qty_base is not None and line.difference_base!=0)
    return render(request,'stocktake_detail.html',{
        'stocktake':stocktake,'lines':lines,'counted_lines':counted,'variance_lines':variance,
    })


# -----------------------------------------------------------------------------
# Data exports / backup
# -----------------------------------------------------------------------------

@login_required
def data_tools(request):
    start,end=_date_range_from_request(request,month_default=True)
    return render(request,'data_tools.html',{'start':start.isoformat(),'end':end.isoformat()})


@login_required
def export_csv(request,kind):
    start,end=_date_range_from_request(request,month_default=True)
    allowed={'sales','stock','purchases','expenses','debts','debt_payments','closings','stocktakes'}
    if kind not in allowed:
        return HttpResponse('Unknown export.',status=404)
    headers,rows=_export_payload(kind,start,end)
    data=_csv_bytes(headers,rows)
    filename=f'msumari_jr_{kind}_{start}_{end}.csv'
    response=HttpResponse(data,content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="{filename}"'
    return response


@login_required
def export_all_zip(request):
    start,end=_date_range_from_request(request,month_default=True)
    buffer=io.BytesIO()
    kinds=['sales','stock','purchases','expenses','debts','debt_payments','closings','stocktakes']
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for kind in kinds:
            headers,rows=_export_payload(kind,start,end)
            archive.writestr(f'{kind}.csv',_csv_bytes(headers,rows))
        archive.writestr('README.txt',f'Msumari jr exports\nPeriod: {start} to {end}\nStock and Debts files are current-position exports.\n')
    response=HttpResponse(buffer.getvalue(),content_type='application/zip')
    response['Content-Disposition']=f'attachment; filename="msumari_jr_exports_{start}_{end}.zip"'
    return response


@admin_required
def backup_download(request):
    engine=settings.DATABASES['default']['ENGINE']
    if engine!='django.db.backends.sqlite3':
        messages.error(request,'Automatic in-app database backup is currently available for SQLite only.')
        return redirect('data_tools')
    db_path=os.fspath(settings.DATABASES['default']['NAME'])
    if not os.path.exists(db_path):
        messages.error(request,'Database file was not found.')
        return redirect('data_tools')

    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(suffix='.sqlite3',delete=False) as temp:
            temp_path=temp.name
        source=sqlite3.connect(db_path)
        destination=sqlite3.connect(temp_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        buffer=io.BytesIO()
        stamp=timezone.localtime().strftime('%Y%m%d_%H%M%S')
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_path,arcname='database/db.sqlite3')
            media_root=os.fspath(getattr(settings,'MEDIA_ROOT','') or '')
            if media_root and os.path.isdir(media_root):
                for root,_,files in os.walk(media_root):
                    for filename in files:
                        full=os.path.join(root,filename)
                        rel=os.path.relpath(full,media_root)
                        archive.write(full,arcname=os.path.join('media',rel))
            archive.writestr('RESTORE_README.txt',
                'Msumari jr backup\n\n1. Stop Django.\n2. Back up the current db.sqlite3.\n3. Replace it with database/db.sqlite3 from this ZIP.\n4. Restore the media folder beside manage.py if needed.\n5. Start Django and run python manage.py check.\n')
        response=HttpResponse(buffer.getvalue(),content_type='application/zip')
        response['Content-Disposition']=f'attachment; filename="Msumari_jr_backup_{stamp}.zip"'
        return response
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------

@admin_required
def users(request):
    users_list = (
        User.objects
        .filter(is_superuser=False)
        .select_related('profile')
        .order_by('username')
    )

    return render(
        request,
        'users.html',
        {
            'users': users_list,
        }
    )

@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'User created.')
        return redirect('users')
    return render(request, 'user_form.html', {'form': form, 'title': 'Add User', 'submit_label': 'Create user'})


@admin_required
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    form = UserEditForm(request.POST or None, instance=target)
    if request.method == 'POST' and form.is_valid():
        becoming_inactive = not form.cleaned_data['is_active']
        becoming_non_admin = form.cleaned_data['role'] != 'admin' and not target.is_superuser
        if target == request.user and becoming_inactive:
            messages.error(request, 'You cannot disable your own account.')
        elif _is_admin_user(target) and _active_admin_count() <= 1 and (becoming_inactive or becoming_non_admin):
            messages.error(request, 'At least one active Admin must remain in the system.')
        else:
            saved = form.save()
            if saved == request.user and form.cleaned_data.get('password'):
                update_session_auth_hash(request, saved)
            messages.success(request, 'User updated.')
            return redirect('users')
    return render(request, 'user_form.html', {
        'form': form, 'target_user': target, 'title': 'Edit User', 'submit_label': 'Save changes'
    })


@admin_required
@require_POST
def user_toggle(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, 'You cannot disable your own account.')
    elif target.is_active and _is_admin_user(target) and _active_admin_count() <= 1:
        messages.error(request, 'At least one active Admin must remain in the system.')
    else:
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        messages.success(request, 'User status updated.')
    return redirect('users')


@admin_required
@require_POST
def user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target == request.user:
        messages.error(request, 'You cannot delete your own account.')
        return redirect('users')
    if target.is_active and _is_admin_user(target) and _active_admin_count() <= 1:
        messages.error(request, 'At least one active Admin must remain in the system.')
        return redirect('users')
    if _user_has_history(target):
        target.is_active = False
        target.save(update_fields=['is_active'])
        messages.info(request, f'{target.username} has transaction history, so the account was disabled instead of deleted.')
    else:
        username = target.username
        target.delete()
        messages.success(request, f'{username} deleted.')
    return redirect('users')


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

@admin_required
def settings_view(request):
    from .models import ShopSettings
    obj = ShopSettings.get_solo()
    form = ShopSettingsForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Settings updated.')
        return redirect('settings')
    return render(request, 'settings.html', {'form': form})

# -----------------------------------------------------------------------------
# QZ Tray trusted printing
# -----------------------------------------------------------------------------

@never_cache
@require_GET
def qz_certificate(request):
    """
    Return the public QZ Tray certificate.

    The public certificate is safe to send to the browser. The matching
    private RSA key must stay on the Django server and must never be placed
    in static files, media, templates, or a public repository.
    """
    cert_path = Path(settings.QZ_CERT_PATH)

    if not cert_path.is_file():
        return HttpResponse(
            (
                'QZ certificate was not found.\n'
                f'Expected location:\n{cert_path}'
            ),
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    try:
        certificate = cert_path.read_text(encoding='utf-8').strip()

        if not certificate:
            return HttpResponse(
                'QZ certificate file is empty.',
                status=500,
                content_type='text/plain; charset=utf-8',
            )

        if 'BEGIN CERTIFICATE' not in certificate:
            return HttpResponse(
                'The QZ certificate does not appear to be a valid PEM certificate.',
                status=500,
                content_type='text/plain; charset=utf-8',
            )

        response = HttpResponse(
            certificate + '\n',
            content_type='text/plain; charset=utf-8',
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response

    except UnicodeDecodeError:
        return HttpResponse(
            'The QZ certificate must be a UTF-8 text file.',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    except OSError as exc:
        return HttpResponse(
            f'Could not read QZ certificate: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )


@login_required
@require_POST
@never_cache
def qz_sign(request):
    """
    Sign one QZ Tray request with the private RSA key using SHA-512.

    The SHA-512 algorithm here must match
    qz.security.setSignatureAlgorithm('SHA512') in the browser.
    """
    private_key_path = Path(settings.QZ_PRIVATE_KEY_PATH)
    data_to_sign = request.body

    try:
        maximum_bytes = int(
            getattr(settings, 'QZ_MAX_SIGNING_BYTES', 1024 * 1024)
        )
    except (TypeError, ValueError):
        maximum_bytes = 1024 * 1024

    if not data_to_sign:
        return HttpResponseBadRequest(
            'No QZ data was supplied for signing.',
            content_type='text/plain; charset=utf-8',
        )

    if len(data_to_sign) > maximum_bytes:
        return HttpResponse(
            'QZ signing request is too large.',
            status=413,
            content_type='text/plain; charset=utf-8',
        )

    if not private_key_path.is_file():
        return HttpResponse(
            (
                'QZ private key was not found.\n'
                f'Expected location:\n{private_key_path}'
            ),
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    try:
        private_key = RSA.import_key(private_key_path.read_bytes())

        if not private_key.has_private():
            return HttpResponse(
                'The configured QZ key is not a private RSA key.',
                status=500,
                content_type='text/plain; charset=utf-8',
            )

        digest = SHA512.new(data_to_sign)
        signature = pkcs1_15.new(private_key).sign(digest)
        encoded_signature = base64.b64encode(signature).decode('ascii')

        response = HttpResponse(
            encoded_signature,
            content_type='text/plain; charset=utf-8',
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response

    except (ValueError, IndexError, TypeError) as exc:
        return HttpResponse(
            f'Invalid QZ private key: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    except OSError as exc:
        return HttpResponse(
            f'Could not read QZ private key: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )



# -----------------------------------------------------------------------------
# Ninaowadai - Manual receivables
# -----------------------------------------------------------------------------

@admin_required
def ninaowadai(request):
    today = timezone.localdate()

    # ---------------------------------------------------------
    # Create manual receivable
    # ---------------------------------------------------------
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        phone = (request.POST.get('phone') or '').strip()
        notes = (request.POST.get('notes') or '').strip()

        amount = D(request.POST.get('amount_owed'))

        if not name:
            messages.error(request, 'Enter the name of the person who owes you.')
            return redirect('ninaowadai')

        if amount <= 0:
            messages.error(request, 'Amount owed must be greater than zero.')
            return redirect('ninaowadai')

        owed_date_raw = (request.POST.get('owed_date') or '').strip()
        due_date_raw = (request.POST.get('due_date') or '').strip()

        owed_date = today
        due_date = None

        if owed_date_raw:
            try:
                owed_date = datetime.strptime(
                    owed_date_raw,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Enter a valid owed date.')
                return redirect('ninaowadai')

        if due_date_raw:
            try:
                due_date = datetime.strptime(
                    due_date_raw,
                    '%Y-%m-%d'
                ).date()
            except ValueError:
                messages.error(request, 'Enter a valid due date.')
                return redirect('ninaowadai')

        PersonalReceivable.objects.create(
            name=name,
            phone=phone,
            amount_owed=amount,
            owed_date=owed_date,
            due_date=due_date,
            notes=notes,
            created_by=request.user,
        )

        messages.success(
            request,
            f'{name} added to Ninaowadai successfully.'
        )

        return redirect('ninaowadai')

    # ---------------------------------------------------------
    # List / search / filter
    # ---------------------------------------------------------
    q = (request.GET.get('q') or '').strip()
    show = (request.GET.get('show') or 'open').strip()

    records = list(
        PersonalReceivable.objects
        .select_related('created_by')
        .annotate(
            dashboard_paid=Sum('payments__amount')
        )
    )

    # Normalize calculated fields.
    for item in records:
        item.dashboard_paid = item.dashboard_paid or Decimal('0')

        remaining = item.amount_owed - item.dashboard_paid

        item.dashboard_balance = (
            remaining if remaining > Decimal('0')
            else Decimal('0')
        )

    # Overall totals BEFORE search/filter.
    total_original = sum(
        (item.amount_owed for item in records),
        Decimal('0')
    )

    total_paid = sum(
        (item.dashboard_paid for item in records),
        Decimal('0')
    )

    total_outstanding = sum(
        (item.dashboard_balance for item in records),
        Decimal('0')
    )

    open_count = sum(
        1 for item in records
        if item.dashboard_balance > 0
    )

    # Search.
    if q:
        needle = q.lower()

        records = [
            item for item in records
            if (
                needle in item.name.lower()
                or needle in (item.phone or '').lower()
                or needle in (item.notes or '').lower()
                or needle in item.reference.lower()
            )
        ]

    # Status filter.
    if show == 'paid':
        records = [
            item for item in records
            if item.dashboard_balance <= 0
        ]

    elif show != 'all':
        records = [
            item for item in records
            if item.dashboard_balance > 0
        ]

    records.sort(
        key=lambda item: (
            -item.dashboard_balance,
            item.name.lower()
        )
    )

    return render(
        request,
        'ninaowadai.html',
        {
            'records': records[:300],
            'q': q,
            'show': show,
            'today': today,
            'total_original': total_original,
            'total_paid': total_paid,
            'total_outstanding': total_outstanding,
            'open_count': open_count,
            'payment_methods': PersonalReceivablePayment.METHOD_CHOICES,
        }
    )


@admin_required
@require_POST
def ninaowadai_payment_create(request, pk):
    receivable = get_object_or_404(
        PersonalReceivable,
        pk=pk
    )

    amount = D(request.POST.get('amount'))
    method = (request.POST.get('method') or 'cash').strip()

    if amount <= 0:
        messages.error(
            request,
            'Payment amount must be greater than zero.'
        )
        return redirect('ninaowadai')

    if method not in dict(PersonalReceivablePayment.METHOD_CHOICES):
        messages.error(
            request,
            'Choose a valid payment method.'
        )
        return redirect('ninaowadai')

    with transaction.atomic():
        receivable = (
            PersonalReceivable.objects
            .select_for_update()
            .get(pk=receivable.pk)
        )

        balance = receivable.balance

        if balance <= 0:
            messages.error(
                request,
                f'{receivable.name} has already paid the full amount.'
            )
            return redirect('ninaowadai')

        if amount > balance:
            messages.error(
                request,
                f'Payment cannot exceed the remaining balance of '
                f'{balance:,.2f} TZS.'
            )
            return redirect('ninaowadai')

        PersonalReceivablePayment.objects.create(
            receivable=receivable,
            amount=amount,
            method=method,
            reference=(
                request.POST.get('reference') or ''
            ).strip(),
            notes=(
                request.POST.get('notes') or ''
            ).strip(),
            received_by=request.user,
        )

    messages.success(
        request,
        f'Payment of {amount:,.2f} TZS recorded for '
        f'{receivable.name}.'
    )

    return redirect('ninaowadai')


@admin_required
@require_POST
def ninaowadai_delete(request, pk):
    receivable = get_object_or_404(
        PersonalReceivable,
        pk=pk
    )

    if receivable.payments.exists():
        messages.error(
            request,
            'This record already has payment history and cannot '
            'be deleted.'
        )

        return redirect('ninaowadai')

    name = receivable.name
    receivable.delete()

    messages.success(
        request,
        f'{name} removed from Ninaowadai.'
    )

    return redirect('ninaowadai')