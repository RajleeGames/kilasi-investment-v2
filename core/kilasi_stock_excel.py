"""KILASI INVESTMENT - A4, black-and-white branch stock list (read-only)."""
from decimal import Decimal
from io import BytesIO
import re

import xlsxwriter
from django.db.models import Sum
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
    """Export all products and quantities for the authorized, selected branch."""
    branch = current_branch(request)
    products = Product.objects.select_related('category', 'supplier').order_by('name', 'id')
    quantities = dict(
        StockBatch.objects.filter(branch=branch)
        .values('product_id')
        .annotate(q=Sum('quantity_remaining_base'))
        .values_list('product_id', 'q')
    )

    output = BytesIO()
    book = xlsxwriter.Workbook(output, {'in_memory': True})
    book.set_properties({
        'title': f'KILASI INVESTMENT - {branch.name} - Stock List',
        'subject': 'Branch product stock list',
        'author': 'KILASI INVESTMENT',
        'company': 'KILASI INVESTMENT',
    })
    sheet = book.add_worksheet('Stock List')

    # Plain, printer-friendly black text on white; no decorative colors.
    sheet.hide_gridlines(2)
    sheet.set_portrait()
    sheet.set_paper(9)  # 9 = A4 (210 x 297 mm), NOT A3.
    sheet.fit_to_pages(1, 0)  # One page wide, as many pages tall as necessary.
    sheet.set_margins(0.30, 0.30, 0.42, 0.45)
    sheet.set_footer('&CPage &P of &N', {'margin': 0.22})
    sheet.center_horizontally()
    sheet.set_zoom(95)
    sheet.freeze_panes(6, 0)
    sheet.repeat_rows(0, 5)  # Repeat brand, branch and column headings on every page.

    font = {'font_name': 'Calibri', 'font_size': 10, 'font_color': '#000000'}
    title = book.add_format({**font, 'font_size': 15, 'bold': True, 'align': 'center', 'valign': 'vcenter'})
    branch_fmt = book.add_format({**font, 'font_size': 11, 'bold': True, 'align': 'center'})
    sub_fmt = book.add_format({**font, 'font_size': 10, 'align': 'center'})
    date_fmt = book.add_format({**font, 'font_size': 9, 'align': 'center'})
    heading = book.add_format({**font, 'bold': True, 'border': 1, 'align': 'left', 'valign': 'vcenter'})
    text_fmt = book.add_format({**font, 'border': 1, 'valign': 'vcenter', 'text_wrap': True})
    qty_formats = {}

    sheet.set_column('A:A', 41)
    sheet.set_column('B:B', 21)
    sheet.set_column('C:C', 27)
    sheet.set_column('D:D', 17)
    sheet.set_row(0, 27)
    sheet.set_row(1, 20)
    sheet.set_row(2, 19)
    sheet.set_row(3, 18)
    sheet.set_row(4, 9)
    sheet.set_row(5, 24)

    now = timezone.localtime(timezone.now())
    sheet.merge_range('A1:D1', 'KILASI INVESTMENT', title)
    sheet.merge_range('A2:D2', f'BRANCH: {branch.name}', branch_fmt)
    sheet.merge_range('A3:D3', 'PRODUCT STOCK LIST', sub_fmt)
    sheet.merge_range('A4:D4', f'Date: {now:%d %B %Y}', date_fmt)

    for column, name in enumerate(('PRODUCT NAME', 'CATEGORY', 'SUPPLIER', 'STOCK QUANTITY')):
        sheet.write_string(5, column, name, heading)

    last_row = 5
    for idx, product in enumerate(products):
        row = 6 + idx
        last_row = row
        sheet.set_row(row, 24)
        sheet.write_string(row, 0, product.name, text_fmt)
        sheet.write_string(row, 1, product.category.name if product.category else '', text_fmt)
        sheet.write_string(row, 2, product.supplier.name if product.supplier else '', text_fmt)

        # The cell stays numeric in Excel, while printing e.g. "12 PCS" or "2.5 KG".
        unit = re.sub(r'[^A-Za-z0-9/]', '', product.base_unit or '')
        if unit not in qty_formats:
            number_format = '#,##0.###' + (f'" {unit}"' if unit else '')
            qty_formats[unit] = book.add_format({
                **font, 'border': 1, 'align': 'center', 'valign': 'vcenter',
                'num_format': number_format,
            })
        quantity = quantities.get(product.pk) or Decimal('0')
        sheet.write_number(row, 3, float(quantity), qty_formats[unit])

    sheet.print_area(0, 0, last_row, 3)
    book.close()

    safe_branch = re.sub(r'[^A-Za-z0-9_-]+', '_', branch.name).strip('_').upper()
    filename = f'KILASI_{safe_branch}_STOCK_A4_{now:%Y%m%d}.xlsx'
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response
