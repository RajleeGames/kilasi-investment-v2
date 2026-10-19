"""STAGING-ONLY display of imported legacy Retail sales; zero mutations.

Only mounted through config.urls_kilasi_stage7c, never the regular URLCONF.
"""
from decimal import Decimal
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.core.paginator import Paginator
from core.models import Branch, Sale, SaleItem, StockBatch, CustomerPayment

ZERO = Decimal('0')


def _admin(request):
    if not (request.user.is_superuser or request.user.is_staff or getattr(getattr(request.user, 'profile', None), 'role', '') == 'admin'):
        raise Http404('Not found')


def _ensure_stage7e():
    """Stop accidentally displaying this preview for older staging archives."""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kilasi_stage7e_activation'")
        if not cursor.fetchone():
            raise Http404('This preview requires the Stage 7E database.')


def _selected_branch(request):
    value = request.GET.get('branch', '').strip()
    # GET-only browsing: NEVER change the Retail session branch or write the database.
    branches = list(Branch.objects.filter(is_active=True).exclude(name__iexact='Main Branch').order_by('name'))
    selected = None
    if value:
        if not value.isdecimal():
            raise Http404('Invalid branch')
        selected = next((b for b in branches if b.pk == int(value)), None)
        if selected is None:
            raise Http404('Unknown Retail branch')
    return branches, selected


def _date_filters(request):
    from_day, to_day = request.GET.get('from', '').strip(), request.GET.get('to', '').strip()
    def parse(v):
        if not v: return None
        try: return date.fromisoformat(v)
        except ValueError: raise Http404('Invalid date')
    first, last = parse(from_day), parse(to_day)
    if first and last and first > last:
        raise Http404('Start date after end date')
    return first, last


def _sales_queryset(branch, first=None, last=None):
    qs = Sale.objects.filter(receipt_no__startswith='KIL-OLD-')
    if branch: qs = qs.filter(branch=branch)
    if first: qs = qs.filter(sold_at__date__gte=first)
    if last: qs = qs.filter(sold_at__date__lte=last)
    return qs


def _context(request):
    _admin(request)
    _ensure_stage7e()
    branches, branch = _selected_branch(request)
    first, last = _date_filters(request)
    return {'branches':branches,'selected_branch':branch,'from_day':first.isoformat() if first else '',
            'to_day':last.isoformat() if last else '', 'today': timezone.localdate(),
            'is_staging_history': True}


@login_required
def dashboard(request):
    ctx = _context(request)
    branch = ctx['selected_branch']
    first, last = _date_filters(request)
    sales = _sales_queryset(branch, first, last)
    active = sales.filter(status='completed')
    totals = active.aggregate(amount=Sum('grand_total'),cost=Sum('cogs_total'),profit=Sum('profit_total'),count=Count('pk'))
    ctx.update({'total_amount':totals['amount'] or ZERO,
                'total_cost':totals['cost'] or ZERO,'gross_profit':totals['profit'] or ZERO,
                'completed_count':totals['count'] or 0,'voided_count':sales.filter(status='voided').count(),
                'total_count':sales.count(),
                'recent_sales':sales.select_related('branch','customer').order_by('-sold_at','-pk')[:12],
                'branch_counts':list(_sales_queryset(None).values('branch__name').annotate(count=Count('id'), total=Sum('grand_total')).order_by('branch__name')),
                'opening_stock':StockBatch.objects.filter(quantity_remaining_base__gt=0, **({'branch':branch} if branch else {})).aggregate(v=Sum('quantity_remaining_base'))['v'] or ZERO,
                })
    # Debt snapshot is deliberately lifetime, not affected by date-filtered sales totals.
    all_active_debts = _sales_queryset(branch).filter(status='completed',payment_method='debt')
    debts = all_active_debts.aggregate(v=Sum('grand_total'),n=Count('id'))
    payments = CustomerPayment.objects.filter(is_voided=False,reference__startswith='KIL-OLD-PAY-')
    if branch: payments = payments.filter(branch=branch)
    historical_paid = payments.aggregate(v=Sum('amount'))['v'] or ZERO
    ctx['historical_debt_total'] = debts['v'] or ZERO
    ctx['historical_debt_receipts'] = debts['n'] or 0
    ctx['historical_debt_paid'] = historical_paid
    ctx['historical_debt_remaining'] = (debts['v'] or ZERO) - historical_paid
    return render(request,'kilasi_legacy_viewer/staging_retail_dashboard.html',ctx)


@login_required
def sales(request):
    ctx = _context(request)
    first,last = _date_filters(request)
    qs = _sales_queryset(ctx['selected_branch'],first,last).select_related('branch','customer').order_by('-sold_at','-pk')
    status = request.GET.get('status','').strip()
    if status not in ('','completed','voided'): raise Http404('Unknown status')
    if status: qs = qs.filter(status=status)
    page = Paginator(qs,40).get_page(request.GET.get('page','1'))
    ctx.update({'page':page,'status':status,'result_count':qs.count()})
    return render(request,'kilasi_legacy_viewer/staging_retail_sales.html',ctx)


@login_required
def receipt(request, pk):
    ctx = _context(request)
    item = get_object_or_404(_sales_queryset(ctx['selected_branch']).select_related('branch','customer'),pk=pk)
    if not item.receipt_no.startswith('KIL-OLD-'):
        raise Http404('Not a legacy sale')
    ctx.update({'sale':item,'items':SaleItem.objects.filter(sale=item).select_related('product','unit').order_by('pk')})
    return render(request,'kilasi_legacy_viewer/staging_retail_receipt.html',ctx)
