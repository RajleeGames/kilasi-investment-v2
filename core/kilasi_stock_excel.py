"""KILASI INVESTMENT: read-only, branch-scoped, print-ready product stock XLSX."""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
import re

import xlsxwriter
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .decorators import admin_required
from .models import Product, StockBatch
from .novman_branch import current_branch


@admin_required
@require_GET
@never_cache
def branch_stock_excel(request):
    # Branch selection and admin-only access are enforced by existing app helpers.
    branch = current_branch(request)
    products = list(Product.objects.select_related('category', 'supplier')
                    .prefetch_related('selling_units').order_by('name', 'id'))
    totals = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    for pid, qty, cost in (StockBatch.objects.filter(branch=branch, quantity_remaining_base__gt=0)
                           .values_list('product_id', 'quantity_remaining_base', 'unit_cost_base')):
        totals[pid][0] += qty
        totals[pid][1] += qty * cost

    file = BytesIO()
    wb = xlsxwriter.Workbook(file, {'in_memory': True})
    wb.set_properties({'title': f'KILASI INVESTMENT - {branch.name} - Products and Stock',
                       'author': 'KILASI INVESTMENT', 'company': 'KILASI INVESTMENT'})
    ws = wb.add_worksheet('Products & Stock')
    navy, slate, gold = '#172840', '#50627A', '#F2BD3D'
    ws.hide_gridlines(2)
    ws.set_tab_color(gold)
    ws.set_landscape(); ws.set_paper(8); ws.fit_to_pages(1, 0)
    ws.set_margins(.25, .25, .4, .4)
    ws.set_header('&CKILASI INVESTMENT  |  STOCK REGISTER')
    ws.set_footer('&LInternal branch inventory&RPage &P of &N')
    ws.repeat_rows(9)
    ws.freeze_panes(10, 3)
    ws.set_zoom(80)

    fmt = lambda **opts: wb.add_format({'font_name': 'Aptos', 'font_size': 9, **opts})
    title = fmt(bold=True, font_size=20, font_color='white', bg_color=navy, valign='vcenter')
    subtitle = fmt(font_size=11, font_color='white', bg_color=slate, valign='vcenter')
    metric_head = fmt(bold=True, font_color=slate, bg_color='#ECF0F5', align='center')
    metric_qty = fmt(bold=True, font_size=17, font_color=navy, bg_color='#ECF0F5',
                     align='center', num_format='#,##0')
    metric_value = fmt(bold=True, font_size=13, font_color=navy, bg_color='#ECF0F5',
                       align='center', num_format='#,##0.00')
    note = fmt(font_color=slate, bg_color='#F3F6FA')
    head = fmt(bold=True, font_color='white', bg_color=navy, text_wrap=True,
               valign='vcenter', bottom=2, bottom_color=gold)
    text_f = [fmt(bg_color=c) for c in ('white', '#F3F6FA')]
    qty_f = [fmt(bg_color=c, num_format='#,##0.###') for c in ('white', '#F3F6FA')]
    money_f = [fmt(bg_color=c, num_format='#,##0.00') for c in ('white', '#F3F6FA')]
    status_f = {
        'OUT': fmt(bold=True, font_color='#B91C1C', bg_color='#FEE2E2'),
        'LOW': fmt(bold=True, font_color='#92400E', bg_color='#FEF3C7'),
        'OK': fmt(bold=True, font_color='#047857', bg_color='#D1FAE5'),
    }
    total_label = fmt(bold=True, font_color='white', bg_color=navy)
    total_number = fmt(bold=True, font_size=11, font_color=navy,
                       bg_color='#FCE6A3', num_format='#,##0.00')
    for i, width in enumerate([7, 19, 34, 20, 23, 23, 12, 16, 16, 13, 19, 22, 18, 20, 15]):
        ws.set_column(i, i, width)
    ws.set_row(0, 27); ws.set_row(1, 18)
    ws.merge_range(0, 0, 1, 14, 'KILASI INVESTMENT', title)
    now = timezone.localtime(timezone.now())
    ws.set_row(2, 24)
    ws.merge_range(2, 0, 2, 14,
                   f'{branch.name}  |  COMPLETE BRANCH PRODUCTS & STOCK  |  {now:%d %b %Y  %H:%M}', subtitle)
    metrics = [(0, 2, 'TOTAL PRODUCTS'), (3, 5, 'IN STOCK'), (6, 8, 'LOW STOCK'),
               (9, 11, 'OUT OF STOCK'), (12, 14, 'STOCK VALUE  •  TZS')]
    ws.set_row(4, 20); ws.set_row(5, 35)
    first, last = 11, max(11, 10 + len(products))
    count_in = sum(totals[p.pk][0] > 0 for p in products)
    count_low = sum(0 < totals[p.pk][0] <= p.low_stock_level for p in products)
    count_out = len(products) - count_in
    total_value = sum((totals[p.pk][1] for p in products), Decimal('0'))
    formulas = [f'=COUNTA(C{first}:C{last})', f'=COUNTIF(H{first}:H{last},">0")',
                f'=COUNTIF(J{first}:J{last},"LOW")', f'=COUNTIF(J{first}:J{last},"OUT")',
                f'=SUM(L{first}:L{last})']
    if not products:
        formulas = ['=0'] * 5
    cached = [len(products), count_in, count_low, count_out, float(total_value)]
    for (c1, c2, label), formula, cache in zip(metrics, formulas, cached):
        ws.merge_range(4, c1, 4, c2, label, metric_head)
        ws.merge_range(5, c1, 5, c2, '', metric_value if c1 == 12 else metric_qty)
        ws.write_formula(5, c1, formula, metric_value if c1 == 12 else metric_qty, cache)
    ws.set_row(7, 22)
    ws.merge_range(7, 0, 7, 14,
                   'Quantities use base units. Stock value uses remaining batch costs. '
                   'Selling price is per displayed selling unit.', note)
    labels = ['NO.', 'BARCODE', 'PRODUCT NAME', 'DIVISION', 'CATEGORY', 'SUPPLIER',
              'BASE UNIT', 'STOCK QTY', 'LOW STOCK LEVEL', 'STATUS',
              'AVG COST/BASE (TZS)', 'STOCK VALUE (TZS)', 'SELLING UNIT',
              'SELLING PRICE (TZS)', 'ACTIVE']
    ws.set_row(9, 33)
    for c, label in enumerate(labels):
        ws.write_string(9, c, label, head)
    for i, p in enumerate(products):
        r = 10 + i
        ws.set_row(r, 20)
        qty, val = totals[p.pk]
        text, num, money = text_f[i % 2], qty_f[i % 2], money_f[i % 2]
        active_units = [u for u in p.selling_units.all() if u.is_active]
        unit = next((u for u in active_units if u.is_default), active_units[0] if active_units else None)
        status = 'OUT' if qty <= 0 else ('LOW' if qty <= p.low_stock_level else 'OK')
        ws.write_number(r, 0, i+1, num)
        for c, value in {1: p.barcode or '', 2: p.name, 3: p.get_retail_division_display(),
                         4: p.category.name, 5: p.supplier.name if p.supplier else '',
                         6: p.base_unit, 12: (unit.symbol or unit.name) if unit else '',
                         14: 'YES' if p.is_active else 'NO'}.items():
            ws.write_string(r, c, str(value), text)  # Formula-injection safe.
        ws.write_number(r, 7, float(qty), num)
        ws.write_number(r, 8, float(p.low_stock_level), num)
        ws.write_string(r, 9, status, status_f[status])
        ws.write_number(r, 10, float(val / qty) if qty else 0, money)
        ws.write_number(r, 11, float(val), money)
        if unit is not None:
            ws.write_number(r, 13, float(unit.selling_price), money)
        else:
            ws.write_blank(r, 13, None, money)
    if products:
        ws.autofilter(9, 0, 9 + len(products), 14)
    end = 10 + len(products)
    ws.set_row(end, 25)
    ws.merge_range(end, 0, end, 10, f'TOTAL STOCK VALUE — {branch.name}', total_label)
    ws.write_formula(end, 11, f'=SUM(L11:L{10+len(products)})' if products else '=0', total_number, float(total_value))
    ws.merge_range(end, 12, end, 14, 'TZS • Current branch', total_label)
    ws.print_area(0, 0, end, 14)
    wb.close()
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '_', branch.name).strip('_').lower()
    filename = f'KILASI_INVESTMENT_{safe_name}_STOCK_{now:%Y%m%d_%H%M}.xlsx'
    result = HttpResponse(file.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    result['Content-Disposition'] = f'attachment; filename="{filename}"'
    result['Cache-Control'] = 'private, no-store'
    return result

