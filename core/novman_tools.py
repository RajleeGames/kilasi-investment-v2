"""Novman V2: admin branch management, branch-scoped exports and full backups.

Do not expose these views to cashiers. Backup contains password hashes and customer data.
"""
import csv
from contextlib import closing
import hashlib
import io
import json
import logging
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, connections, transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .decorators import admin_required
from .models import (
    Branch, CashClosing, Customer, CustomerPayment, Expense,
    Product, Purchase, Sale, SaleItem, ShopSettings, StockAdjustment,
    StockBatch, Stocktake, StockTransfer, UserProfile,
)
from .novman_branch import current_branch

logger = logging.getLogger(__name__)


def _export_dates(request):
    today = timezone.localdate()
    start_default = today.replace(day=1)
    def parse(value, default):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return default
    start = parse(request.GET.get('start'), start_default)
    end = parse(request.GET.get('end'), today)
    if start > end:
        start, end = end, start
    return start, end


def _fmt(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S') if timezone.is_aware(value) else value.isoformat(sep=' ')
    return str(value)


def _csv_safe(value):
    """Prevent Excel/LibreOffice formula execution from user-entered names."""
    text = _fmt(value)
    if isinstance(value, (Decimal, int, float)):
        return text
    if text and text.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + text
    if text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(item) for item in row])
    return stream.getvalue().encode('utf-8-sig')


# "stock" and "products" are current snapshots; other reports use selected dates.
EXPORT_KINDS = (
    ('sales', 'Sales'),
    ('sale_items', 'Sale items'),
    ('stock', 'Current stock'),
    ('products', 'Product catalogue'),
    ('purchases', 'Purchases'),
    ('expenses', 'Expenses'),
    ('debts', 'Debt balances by customer'),
    ('debt_payments', 'Customer debt payments'),
    ('transfers', 'Stock transfers'),
    ('adjustments', 'Stock adjustments'),
    ('stocktakes', 'Stocktakes'),
    ('closings', 'Saved cash closings'),
)


def _report(kind, branch, start, end):
    """Every finance/inventory query is restricted to the selected branch."""
    if kind == 'sales':
        q = Sale.objects.filter(branch=branch, sold_at__date__range=(start, end)).select_related('cashier', 'customer').order_by('sold_at', 'id')
        return (['Receipt', 'Date', 'Branch', 'Cashier', 'Customer', 'Payment', 'Status', 'Subtotal TZS', 'Item discounts TZS', 'Bill discount TZS', 'Revenue TZS', 'COGS TZS', 'Profit TZS'],
                ((s.receipt_no, s.sold_at, branch.name, s.cashier.username, s.customer.name if s.customer_id else s.customer_name,
                  s.get_payment_method_display(), s.status, s.subtotal, s.item_discount_total, s.sale_discount,
                  s.grand_total, s.cogs_total, s.profit_total) for s in q))
    if kind == 'sale_items':
        q = SaleItem.objects.filter(sale__branch=branch, sale__sold_at__date__range=(start, end)).select_related('sale', 'product', 'unit').order_by('sale__sold_at', 'id')
        return (['Receipt', 'Date', 'Status', 'Product ID', 'Product', 'Unit', 'Quantity', 'Base quantity', 'Unit selling price TZS', 'Line discount TZS', 'Line revenue TZS', 'Line COGS TZS'],
                ((x.sale.receipt_no, x.sale.sold_at, x.sale.status, x.product_id, x.product.name, x.unit.symbol,
                  x.quantity, x.base_quantity, x.normal_unit_price, x.discount_amount, x.final_total, x.cost_total) for x in q))
    if kind == 'stock':
        q = StockBatch.objects.filter(branch=branch, quantity_remaining_base__gt=0).select_related('product', 'product__category').order_by('product__name', 'id')
        return (['Branch', 'Product ID', 'Product', 'Barcode', 'Category', 'Batch ID', 'Source', 'Reference', 'Quantity remaining', 'Base unit', 'Buying price per base unit TZS', 'Stock buying value TZS', 'Received at'],
                ((branch.name, b.product_id, b.product.name, b.product.barcode, b.product.category.name, b.id, b.source,
                  b.reference, b.quantity_remaining_base, b.product.base_unit, b.unit_cost_base,
                  b.quantity_remaining_base * b.unit_cost_base, b.received_at) for b in q))
    if kind == 'products':
        q = Product.objects.select_related('category', 'supplier').prefetch_related('selling_units').order_by('name', 'id')
        return (['Product ID', 'Name', 'Barcode', 'Category', 'Supplier', 'Base unit', 'Last buying price TZS', 'Selling units/prices TZS', 'Low stock threshold', 'Active'],
                ((p.id, p.name, p.barcode, p.category.name, p.supplier.name if p.supplier_id else '', p.base_unit,
                  p.last_buying_price, '; '.join(f'{u.name}: {u.selling_price}' for u in p.selling_units.all()),
                  p.low_stock_level, p.is_active) for p in q))
    if kind == 'purchases':
        q = Purchase.objects.filter(branch=branch, purchased_at__date__range=(start, end)).select_related('created_by').prefetch_related('items__product', 'items__unit').order_by('purchased_at', 'id')
        def rows():
            for p in q:
                for x in p.items.all():
                    yield (f'PUR-{p.id:05d}', p.purchased_at, branch.name, p.supplier_name, p.invoice_number,
                           x.product_id, x.product.name, x.unit.symbol, x.quantity, x.base_quantity, x.cost_per_unit,
                           x.total_cost, p.created_by.username, p.notes)
        return (['Purchase', 'Date', 'Branch', 'Supplier', 'Invoice', 'Product ID', 'Product', 'Unit', 'Quantity', 'Base quantity', 'Buying price TZS', 'Total cost TZS', 'Recorded by', 'Notes'], rows())
    if kind == 'expenses':
        q = Expense.objects.filter(branch=branch, expense_date__range=(start, end)).select_related('created_by').order_by('expense_date', 'id')
        return (['Date', 'Branch', 'Category', 'Description', 'Amount TZS', 'By'],
                ((e.expense_date, branch.name, e.get_category_display(), e.description, e.amount, e.created_by.username) for e in q))
    if kind == 'debts':
        # Payments and debt sales are allocated by branch. No cross-branch total shown as if it were branch-only.
        credit_qs = Sale.objects.filter(branch=branch, status='completed', payment_method='debt')
        debt_map = {r['customer_id']: r['amount'] for r in credit_qs.exclude(customer_id=None).values('customer_id').annotate(amount=Sum('grand_total'))}
        pay_map = {r['customer_id']: r['amount'] for r in CustomerPayment.objects.filter(branch=branch, is_voided=False).values('customer_id').annotate(amount=Sum('amount'))}
        ids = set(debt_map) | set(pay_map)
        q = Customer.objects.filter(pk__in=ids).order_by('name', 'id')
        return (['Customer ID', 'Customer', 'Phone', 'Branch', 'Lifetime branch credit TZS', 'Lifetime branch repayments TZS', 'Branch ledger balance TZS'],
                ((c.id, c.name, c.phone, branch.name, debt_map.get(c.id, Decimal('0')),
                  pay_map.get(c.id, Decimal('0')), debt_map.get(c.id, Decimal('0')) - pay_map.get(c.id, Decimal('0'))) for c in q))
    if kind == 'debt_payments':
        q = CustomerPayment.objects.filter(branch=branch, paid_at__date__range=(start, end)).select_related('customer', 'received_by').order_by('paid_at', 'id')
        return (['Payment ID', 'Date', 'Branch', 'Customer', 'Amount TZS', 'Method', 'Reference', 'Voided', 'Received by', 'Notes'],
                ((p.id, p.paid_at, branch.name, p.customer.name, p.amount, p.get_method_display(),
                  p.reference, p.is_voided, p.received_by.username, p.notes) for p in q))
    if kind == 'transfers':
        q = StockTransfer.objects.filter(Q(source_branch=branch) | Q(destination_branch=branch), created_at__date__range=(start, end)).select_related('source_branch', 'destination_branch', 'product', 'created_by').order_by('created_at', 'id')
        return (['Transfer', 'Date', 'Source', 'Destination', 'Product ID', 'Product', 'Base quantity', 'By', 'Notes'],
                ((f'TRF-{t.id:05d}', t.created_at, t.source_branch.name, t.destination_branch.name,
                  t.product_id, t.product.name, t.quantity_base, t.created_by.username, t.notes) for t in q))
    if kind == 'adjustments':
        q = StockAdjustment.objects.filter(branch=branch, created_at__date__range=(start, end)).select_related('product', 'created_by').order_by('created_at', 'id')
        return (['Adjustment ID', 'Date', 'Branch', 'Product ID', 'Product', 'Type', 'Base quantity', 'Reason', 'By'],
                ((a.id, a.created_at, branch.name, a.product_id, a.product.name, a.adjustment_type, a.quantity_base, a.reason, a.created_by.username) for a in q))
    if kind == 'stocktakes':
        q = Stocktake.objects.filter(branch=branch, count_date__range=(start, end)).select_related('created_by', 'posted_by').prefetch_related('lines').order_by('count_date', 'id')
        return (['Stocktake', 'Count date', 'Branch', 'Status', 'Products', 'Variance lines', 'Created by', 'Posted by', 'Notes'],
                ((x.reference, x.count_date, branch.name, x.status, len(x.lines.all()),
                  sum(1 for l in x.lines.all() if l.counted_qty_base is not None and l.difference_base != 0),
                  x.created_by.username, x.posted_by.username if x.posted_by_id else '', x.notes) for x in q))
    if kind == 'closings':
        q = CashClosing.objects.filter(branch=branch, close_date__range=(start, end)).select_related('cashier', 'closed_by').order_by('close_date', 'id')
        return (['Closing', 'Date', 'Branch', 'Cashier', 'Cash sales TZS', 'Mobile sales TZS', 'Bank sales TZS', 'Debt sales TZS', 'Debt cash collected TZS', 'Expected cash TZS', 'Counted cash TZS', 'Difference TZS', 'Recorded by'],
                ((c.reference, c.close_date, branch.name, c.cashier.username, c.cash_sales, c.mobile_money_sales, c.bank_sales,
                  c.debt_sales, c.debt_cash_collected, c.expected_cash, c.counted_cash, c.difference, c.closed_by.username) for c in q))
    raise Http404('Unknown export type.')


@admin_required
@require_http_methods(['GET', 'POST'])
def branch_manage(request):
    if request.method == 'POST':
        messages.error(request, 'The business currently has three fixed Retail branches. Contact the administrator before restructuring branches.')
        return redirect('branch_manage')
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create':
            name = ' '.join((request.POST.get('name') or '').split())
            if not name or len(name) > 120:
                messages.error(request, 'Enter a branch name (maximum 120 characters).')
            elif Branch.objects.filter(name__iexact=name).exists():
                messages.error(request, 'A branch with this name already exists.')
            else:
                try:
                    with transaction.atomic():
                        Branch.objects.create(name=name, is_active=True)
                    messages.success(request, f'Branch {name} created. You can now receive or transfer stock into it.')
                except IntegrityError:
                    messages.error(request, 'This branch name is already in use.')
        elif action == 'deactivate':
            branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'))
            if not branch.is_active:
                messages.info(request, 'Branch is already inactive.')
            elif Branch.objects.filter(is_active=True).count() <= 1:
                messages.error(request, 'Cannot deactivate the last active branch.')
            elif UserProfile.objects.filter(branch=branch, user__is_active=True).exists():
                messages.error(request, 'Reassign or disable staff accounts in this branch first.')
            elif StockBatch.objects.filter(branch=branch, quantity_remaining_base__gt=0).exists():
                messages.error(request, 'Branch still has stock. Transfer stock out before deactivating.')
            elif (Sale.objects.filter(branch=branch).exists() or Purchase.objects.filter(branch=branch).exists()
                  or Expense.objects.filter(branch=branch).exists() or CustomerPayment.objects.filter(branch=branch).exists()
                  or Stocktake.objects.filter(branch=branch).exists() or StockAdjustment.objects.filter(branch=branch).exists()
                  or StockTransfer.objects.filter(Q(source_branch=branch) | Q(destination_branch=branch)).exists()):
                messages.error(request, 'This branch has business history and cannot be hidden from branch reports.')
            else:
                branch.is_active = False
                branch.save(update_fields=['is_active'])
                messages.success(request, 'Empty unused branch deactivated.')
        elif action == 'activate':
            branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'))
            if not branch.is_active:
                branch.is_active = True
                branch.save(update_fields=['is_active'])
            messages.success(request, f'{branch.name} is active.')
        else:
            messages.error(request, 'Invalid branch operation.')
        return redirect('branch_manage')
    branch_info = []
    for branch in Branch.objects.filter(name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA')).order_by('-is_active', 'name'):
        quantities = StockBatch.objects.filter(branch=branch, quantity_remaining_base__gt=0).aggregate(q=Sum('quantity_remaining_base'))['q'] or Decimal('0')
        branch_info.append({'branch': branch, 'stock': quantities,
                            'users': UserProfile.objects.filter(branch=branch, user__is_active=True).count()})
    return render(request, 'novman_branches.html', {'branch_info': branch_info})


@admin_required
@require_GET
def data_tools(request):
    start, end = _export_dates(request)
    return render(request, 'data_tools.html', {
        'start': start.isoformat(), 'end': end.isoformat(), 'branch': current_branch(request),
        'export_kinds': EXPORT_KINDS,
    })


@admin_required
@require_GET
@never_cache
def export_csv(request, kind):
    branch = current_branch(request)
    start, end = _export_dates(request)
    headers, rows = _report(kind, branch, start, end)
    response = HttpResponse(_csv_bytes(headers, rows), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="novman_branch_{branch.pk}_{kind}_{start}_{end}.csv"'
    response['Cache-Control'] = 'no-store, private'
    return response


@admin_required
@require_GET
@never_cache
def export_all_zip(request):
    branch = current_branch(request)
    start, end = _export_dates(request)
    output = tempfile.SpooledTemporaryFile(max_size=12 * 1024 * 1024, mode='w+b')
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for kind, _ in EXPORT_KINDS:
                headers, rows = _report(kind, branch, start, end)
                bundle.writestr(f'{kind}.csv', _csv_bytes(headers, rows))
            bundle.writestr('README.txt',
                f'NOVMAN AUTO SPARES V2\nBranch: {branch.name} (ID {branch.pk})\nPeriod: {start} to {end}\n'
                'Stock, products and debt balances show current/lifetime positions, not only the date range.\n'
                'Saved closings CSV contains only actual posted closing records; Novman V2 End Day is currently a read-only summary.\n'
                'This CSV ZIP is for analysis, NOT a database restoration backup.\n')
        output.seek(0)
        response = FileResponse(output, as_attachment=True,
                                filename=f'novman_branch_{branch.pk}_exports_{start}_{end}.zip',
                                content_type='application/zip')
        response['Cache-Control'] = 'no-store, private'
        return response
    except Exception:
        output.close()
        raise


@admin_required
@require_GET
@never_cache
def backup_download(request):
    """Consistent full SQLite snapshot + media. Both branches, not only current one."""
    if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
        messages.error(request, 'Full backup currently supports SQLite databases only.')
        return redirect('data_tools')

    output = tempfile.SpooledTemporaryFile(max_size=12 * 1024 * 1024, mode='w+b')
    try:
        with tempfile.TemporaryDirectory(prefix='novman_backup_') as folder:
            snapshot = Path(folder) / 'novman.sqlite3'
            # Backup from the live Django connection (including WAL transactions),
            # rather than copying a possibly active .sqlite3 file.
            db = connections['default']
            db.ensure_connection()
            with closing(sqlite3.connect(snapshot)) as dest:
                db.connection.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite backup integrity failed.')
            checksum = hashlib.sha256()
            with snapshot.open('rb') as source_file:
                for chunk in iter(lambda: source_file.read(1024 * 1024), b''):
                    checksum.update(chunk)
            digest = checksum.hexdigest()
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(snapshot, 'database/db.sqlite3')
                media = Path(settings.MEDIA_ROOT)
                if media.is_dir():
                    for root, dirs, files in os.walk(media, followlinks=False):
                        dirs[:] = [d for d in dirs if not (Path(root)/d).is_symlink()]
                        for name in files:
                            path = Path(root) / name
                            if not path.is_file() or path.is_symlink():
                                continue
                            bundle.write(path, 'media/' + path.relative_to(media).as_posix())
                manifest = {
                    'business': ShopSettings.get_solo().name,
                    'product': 'Novman Auto Spares V2',
                    'created_at': timezone.now().isoformat(),
                    'scope': 'All branches, all dates, full SQLite database and uploaded media',
                    'database_sha256': digest,
                    'branches': list(Branch.objects.values_list('name', flat=True)),
                    'products': Product.objects.count(),
                    'note': 'Confidential: includes staff password hashes and customer information.',
                }
                bundle.writestr('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))
                bundle.writestr('RESTORE_README.txt',
                    'NOVMAN AUTO SPARES V2 - FULL BACKUP\n'
                    'Contains all branches and all historical data.\n\n'
                    'Restore only while the application is stopped. Back up the current live DB before any replacement.\n'
                    'Replace the SQLite file configured by NOVMAN_DB_PATH with database/db.sqlite3.\n'
                    'Restore media/ to the configured MEDIA_ROOT. Set owner/group permissions for the service user.\n'
                    'Run manage.py check and validate products/stock totals before bringing POS back online.\n'
                    'Keep this ZIP encrypted and offsite.\n')
        output.seek(0)
        stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        response = FileResponse(output, as_attachment=True,
                                filename=f'novman_auto_spares_full_backup_{stamp}.zip',
                                content_type='application/zip')
        response['Cache-Control'] = 'no-store, private'
        return response
    except Exception:
        output.close()
        logger.exception('Novman full backup failed.')
        raise
