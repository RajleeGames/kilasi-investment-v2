"""Admin-only, query-only Phase 7B archive browser.

Original business records are NOT posted to live models, and SMS bodies omitted.
The fixed whitelisted SQL below never interpolates untrusted table/column identifiers.
"""
from contextlib import closing
import sqlite3

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render


def archival_conn():
    filename = settings.DATABASES['default']['NAME']
    c = sqlite3.connect(__import__('pathlib').Path(filename).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def admin_only(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied('Only a superuser may inspect the private historical archive.')
        return view(request, *args, **kwargs)
    return wrapped


# Labels and SQL are constants: no dynamic table names or arbitrary SQL from browser.
SECTIONS = {
 'sales': {'name': 'Retail invoices', 'sql': '''
     SELECT i.id,i.created_at,i.invoice_state,i.status AS payment_method,i.total_amount,
       COALESCE(c.name,'Walk-in') AS customer, COALESCE(b.name,'Unassigned') AS branch
     FROM kilasi_legacy_sales_invoice i
     LEFT JOIN kilasi_legacy_sales_customer c ON c.id=i.customer_id
     LEFT JOIN kilasi_legacy_inventory_branch b ON b.id=i.branch_id
     WHERE (:q='' OR CAST(i.id AS TEXT)=:q)
     ORDER BY i.id DESC''',
     'headers': [('id','Old invoice #'),('created_at','Date'),('invoice_state','State'),('payment_method','Payment'),('customer','Customer'),('branch','Retail branch'),('total_amount','TZS')],
     'kind':'invoice'},
 'debts': {'name': 'Retail debt invoices', 'sql': '''
     SELECT i.id,i.created_at,i.status AS payment_method,i.invoice_state,i.total_amount,
       COALESCE(SUM(p.amount),0) AS payments,
       MAX(0,i.total_amount-COALESCE(SUM(p.amount),0)) AS balance,
       COALESCE(c.name,'Unknown') AS customer,
       COALESCE(b.name,'Unassigned') AS branch
     FROM kilasi_legacy_sales_invoice i
     LEFT JOIN kilasi_legacy_sales_payment p ON p.invoice_id=i.id
     LEFT JOIN kilasi_legacy_sales_customer c ON c.id=i.customer_id
     LEFT JOIN kilasi_legacy_inventory_branch b ON b.id=i.branch_id
     WHERE i.status='debt' AND i.invoice_state='active'
       AND (:q='' OR CAST(i.id AS TEXT)=:q)
     GROUP BY i.id ORDER BY i.id DESC''',
     'headers':[('id','Old invoice #'),('created_at','Date'),('customer','Customer'),('branch','Retail branch'),('total_amount','Total TZS'),('payments','Paid TZS'),('balance','Balance TZS')],
     'kind':'invoice'},
 'trips': {'name': 'HAMA NA KILASI trips', 'sql': '''
     SELECT t.id,t.trip_date,t.customer_name,t.origin,t.destination,t.status,t.amount_charged,
       COALESCE(v.plate_number,CAST(t.vehicle_id AS TEXT),'—') AS vehicle,
       COALESCE(NULLIF(TRIM(COALESCE(d.first_name,'') || ' ' || COALESCE(d.last_name,'')),''), CAST(t.driver_id AS TEXT),'—') AS driver
     FROM kilasi_legacy_transport_trip t
     LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=t.vehicle_id
     LEFT JOIN kilasi_legacy_transport_driver d ON d.id=t.driver_id
     WHERE (:q='' OR CAST(t.id AS TEXT)=:q)
     ORDER BY t.id DESC''',
     'headers':[('id','Old trip #'),('trip_date','Trip date'),('customer_name','Customer'),('origin','From'),('destination','To'),('vehicle','Vehicle'),('driver','Driver'),('status','Status'),('amount_charged','Charged TZS')],
     'kind':'table'},
 'bookings': {'name': 'HAMA NA KILASI bookings', 'sql': '''
     SELECT b.id,b.booking_datetime,b.customer_name,b.pickup_area,b.destination,b.status
     FROM kilasi_legacy_transport_booking b
     WHERE (:q='' OR CAST(b.id AS TEXT)=:q) ORDER BY b.id DESC''',
     'headers':[('id','Old booking #'),('booking_datetime','Booking date'),('customer_name','Customer'),('pickup_area','Pickup'),('destination','Destination'),('status','Status')],
     'kind':'table'},
 'transport-expenses': {'name': 'HAMA NA KILASI expenses', 'sql': '''
     SELECT e.id,e.expense_date,e.expense_type,e.description,e.amount,
            COALESCE(v.plate_number,'General') AS vehicle
     FROM kilasi_legacy_transport_transportexpense e
     LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=e.vehicle_id
     WHERE (:q='' OR CAST(e.id AS TEXT)=:q) ORDER BY e.id DESC''',
     'headers':[('id','Old expense #'),('expense_date','Date'),('expense_type','Category'),('description','Description'),('vehicle','Vehicle'),('amount','TZS')],
     'kind':'table'},
 'sms': {'name':'Historical SMS delivery log', 'sql': '''
     SELECT m.id,m.created_at,m.status,m.sent_at,m.delivered_at,
            CASE WHEN m.request_id IS NULL OR m.request_id='' THEN '—' ELSE 'Recorded' END AS provider_reference
     FROM kilasi_legacy_sms_smsmessage m
     WHERE (:q='' OR CAST(m.id AS TEXT)=:q) ORDER BY m.id DESC''',
     'headers':[('id','Old message #'),('created_at','Created'),('status','Status'),('sent_at','Sent at'),('delivered_at','Delivered at'),('provider_reference','Provider ref')],
     'kind':'table'},
 'genji': {'name':'Historical Genji (all 647)', 'sql': '''
     SELECT g.id,g.sell_date,g.buy_date,g.item_name,g.quantity,g.buying_cost,g.selling_price,
       (g.selling_price-g.buying_cost)*g.quantity AS profit,
       CASE WHEN substr(g.sell_date,1,4)='0026' THEN 'DATE REVIEW' ELSE 'STAGED' END AS import_status
     FROM kilasi_legacy_sales_genjisale g
     WHERE (:q='' OR CAST(g.id AS TEXT)=:q) ORDER BY g.id DESC''',
     'headers':[('id','Old Genji #'),('buy_date','Bought'),('sell_date','Sold'),('item_name','Item'),('quantity','Qty'),('buying_cost','Buying/unit'),('selling_price','Selling/unit'),('profit','Profit TZS'),('import_status','Status')],
     'kind':'table'},
 'topups': {'name':'Historical Top-Up exchanges', 'sql': '''
     SELECT t.id,t.created_at,t.new_product_qty,t.used_item_name,t.used_item_category,
       t.used_item_buying_price,t.addon_amount,t.status
     FROM kilasi_legacy_topup_topuptransaction t
     WHERE (:q='' OR CAST(t.id AS TEXT)=:q) ORDER BY t.id DESC''',
     'headers':[('id','Old Top-Up #'),('created_at','Date'),('new_product_qty','New qty'),('used_item_name','Received item'),('used_item_category','Category'),('used_item_buying_price','Used value TZS'),('addon_amount','Cash added TZS'),('status','Status')],
     'kind':'table'},
 'stock-entries': {'name':'Old stock movement audit', 'sql': '''
     SELECT s.id,s.timestamp,p.name AS product,b.name AS branch,s.change,s.note
     FROM kilasi_legacy_inventory_stockentry s
     LEFT JOIN kilasi_legacy_inventory_product p ON p.id=s.product_id
     LEFT JOIN kilasi_legacy_inventory_branch b ON b.id=s.branch_id
     WHERE (:q='' OR CAST(s.id AS TEXT)=:q) ORDER BY s.id DESC''',
     'headers':[('id','Old entry #'),('timestamp','Date'),('product','Product'),('branch','Retail branch'),('change','Qty change'),('note','Note')],
     'kind':'table'},
 'personal-debts': {'name':'Old personal receivables', 'sql': '''
     SELECT d.id,d.name,d.amount_owed,d.created_at,d.is_active,d.description
     FROM kilasi_legacy_sales_mydebt d
     WHERE (:q='' OR CAST(d.id AS TEXT)=:q) ORDER BY d.id DESC''',
     'headers':[('id','Old debt #'),('name','Customer'),('amount_owed','TZS'),('created_at','Created'),('is_active','Active'),('description','Description')],
     'kind':'table'}
}


def summary(c):
    queries = [
        ('Retail invoices', 'SELECT COUNT(*) FROM kilasi_legacy_sales_invoice', 'sales'),
        ('Unpaid debt invoices', "SELECT COUNT(*) FROM kilasi_legacy_sales_invoice WHERE status='debt' AND invoice_state='active'", 'debts'),
        ('Transport trips', 'SELECT COUNT(*) FROM kilasi_legacy_transport_trip', 'trips'),
        ('Transport bookings', 'SELECT COUNT(*) FROM kilasi_legacy_transport_booking', 'bookings'),
        ('Transport expenses', 'SELECT COUNT(*) FROM kilasi_legacy_transport_transportexpense', 'transport-expenses'),
        ('SMS history', 'SELECT COUNT(*) FROM kilasi_legacy_sms_smsmessage', 'sms'),
        ('Genji history', 'SELECT COUNT(*) FROM kilasi_legacy_sales_genjisale', 'genji'),
        ('Top-Up', 'SELECT COUNT(*) FROM kilasi_legacy_topup_topuptransaction', 'topups'),
        ('Old stock movements', 'SELECT COUNT(*) FROM kilasi_legacy_inventory_stockentry', 'stock-entries'),
        ('Personal debts', 'SELECT COUNT(*) FROM kilasi_legacy_sales_mydebt', 'personal-debts'),
    ]
    return [(label,c.execute(sql).fetchone()[0],slug) for label,sql,slug in queries]


@admin_only
def overview(request):
    with closing(archival_conn()) as c:
        cards = summary(c)
        status = c.execute('SELECT phase, operational_activation_allowed, sms_enabled FROM kilasi_legacy_manifest WHERE id=1').fetchone()
    return render(request,'kilasi_legacy_viewer/overview.html',{'cards':cards,'manifest':status})


@admin_only
def records(request,section):
    cfg=SECTIONS.get(section)
    if not cfg: raise Http404('Unknown history section')
    q = (request.GET.get('q') or '').strip()
    if len(q)>24: q=q[:24]
    try: page=max(1,min(999999,int(request.GET.get('page','1'))))
    except ValueError: page=1
    # Built SQL is fully static. Only values bound to named parameters.
    base=cfg['sql']
    with closing(archival_conn()) as c:
        amount=c.execute('SELECT COUNT(*) FROM ('+base+')',{'q':q}).fetchone()[0]
        maxpage=max(1,(amount+39)//40)
        page=min(page,maxpage)
        rows=c.execute('SELECT * FROM ('+base+') LIMIT :lim OFFSET :off',{'q':q,'lim':40,'off':(page-1)*40}).fetchall()
    cols=[x[1] for x in cfg['headers']]
    ordered=[[_kilasi_display_archive_cell(f, dict(row)[f]) for f,_ in cfg['headers']] for row in rows]
    ids=[dict(row)['id'] for row in rows]
    return render(request,'kilasi_legacy_viewer/records.html',
        {'section':section,'name':cfg['name'],'kind':cfg['kind'],'headers':cols,
         'rows':list(zip(ids,ordered)),'count':amount,'page':page,'pages':maxpage,'q':q,
         'prev':page-1 if page>1 else None,'next':page+1 if page<maxpage else None})


@admin_only
def invoice_lines(request, invoice_id):
    with closing(archival_conn()) as c:
        header=c.execute('''SELECT i.id,i.created_at,i.total_amount,i.status,i.invoice_state,
          COALESCE(c.name,'Walk-in') AS customer FROM kilasi_legacy_sales_invoice i
          LEFT JOIN kilasi_legacy_sales_customer c ON c.id=i.customer_id WHERE i.id=?''',(invoice_id,)).fetchone()
        if not header: raise Http404('Invoice not found')
        lines=c.execute('''SELECT il.id,p.name AS product,il.quantity,il.selling_price,il.buying_cost,
            il.discount FROM kilasi_legacy_sales_invoiceitem il
            LEFT JOIN kilasi_legacy_inventory_product p ON p.id=il.product_id
            WHERE il.invoice_id=? ORDER BY il.id''',(invoice_id,)).fetchall()
        payments=c.execute('''SELECT id,amount,timestamp FROM kilasi_legacy_sales_payment
            WHERE invoice_id=? ORDER BY timestamp''',(invoice_id,)).fetchall()
    return render(request,'kilasi_legacy_viewer/invoice.html',
       {'header':header,'lines':lines,'payments':payments})


def _kilasi_display_archive_cell(field, value):
    """Human-readable amounts on read-only historical lists only."""
    if value is None or value=='': return '—'
    if field in {'amount','total_amount','payments','balance','amount_charged',
                 'buying_cost','selling_price','profit','used_item_buying_price',
                 'addon_amount','amount_owed','buying_price','asking_price','sold_price'}:
        try:
            from decimal import Decimal
            return f'{Decimal(str(value)):,.0f}'
        except (ValueError,ArithmeticError):return value
    return value
