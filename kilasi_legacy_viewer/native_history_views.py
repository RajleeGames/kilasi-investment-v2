"""Native V2 look for historical records, exposed only by the isolated Stage 7C URLCONF.

NO INSERT/UPDATE/DELETE and no provider interactions. Trips are NOT outstanding balances.
"""
from contextlib import closing
from decimal import Decimal
from django.shortcuts import render
from django.http import Http404
from .views import admin_only, archival_conn, records, SECTIONS, _kilasi_display_archive_cell


def _n(c, name):
    return c.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]

@admin_only
def transport_home(request):
    with closing(archival_conn()) as c:
        trips = _n(c, 'kilasi_legacy_transport_trip')
        bookings = _n(c, 'kilasi_legacy_transport_booking')
        vehicles = _n(c, 'kilasi_legacy_transport_vehicle')
        drivers = _n(c, 'kilasi_legacy_transport_driver')
        expenses = _n(c, 'kilasi_legacy_transport_transportexpense')
        charged = c.execute("SELECT COALESCE(SUM(amount_charged),0) FROM kilasi_legacy_transport_trip WHERE status!='cancelled'").fetchone()[0]
        expense_total = c.execute('SELECT COALESCE(SUM(amount),0) FROM kilasi_legacy_transport_transportexpense').fetchone()[0]
        statuses = c.execute('SELECT status,COUNT(*) FROM kilasi_legacy_transport_trip GROUP BY status ORDER BY status').fetchall()
        latest = c.execute('''SELECT t.id,t.trip_date,t.origin,t.destination,t.status,t.amount_charged,
            COALESCE(v.plate_number,'—') AS vehicle
            FROM kilasi_legacy_transport_trip t LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=t.vehicle_id
            ORDER BY t.id DESC LIMIT 12''').fetchall()
    return render(request, 'kilasi_legacy_viewer/native_transport.html', {
        'trips': trips, 'bookings': bookings, 'vehicles': vehicles, 'drivers': drivers,
        'expenses': expenses, 'charged': charged, 'expense_total': expense_total,
        'statuses': statuses, 'latest': latest,
    })

@admin_only
def trade_home(request):
    with closing(archival_conn()) as c:
        genji_total = _n(c, 'kilasi_legacy_sales_genjisale')
        topups = _n(c, 'kilasi_legacy_topup_topuptransaction')
        used_stock = _n(c, 'kilasi_legacy_topup_usedtopupstock')
        used_sales = _n(c, 'kilasi_legacy_topup_usedtopupsale')
        used_available = c.execute("SELECT COUNT(*) FROM kilasi_legacy_topup_usedtopupstock WHERE status='available'").fetchone()[0]
        addon = c.execute('SELECT COALESCE(SUM(addon_amount),0) FROM kilasi_legacy_topup_topuptransaction').fetchone()[0]
    return render(request, 'kilasi_legacy_viewer/native_trade.html', {
        'genji_total': genji_total, 'topups': topups, 'used_stock': used_stock,
        'used_sales': used_sales, 'used_available': used_available, 'addon': addon,
    })

@admin_only
def used_stock(request):
    # Generic stage-only view, intentionally NEVER creates/resells a used item.
    cfg = {
        'name': 'Used stock — historical', 'kind': 'table',
        'headers': [('id','Old stock #'), ('item_name','Item'), ('item_category','Category'),
                    ('condition','Condition'), ('status','Status'), ('buying_price','Buying TZS'),
                    ('asking_price','Asking TZS'), ('sold_price','Sold TZS')],
        'sql': '''SELECT id,item_name,item_category,condition,status,buying_price,asking_price,sold_price
                  FROM kilasi_legacy_topup_usedtopupstock
                  WHERE (:q='' OR CAST(id AS TEXT)=:q) ORDER BY id DESC''',
    }
    q = request.GET.get('q', '').strip()[:24]
    try:
        page = max(1,min(1000000,int(request.GET.get('page','1'))))
    except (ValueError,TypeError):
        page=1
    with closing(archival_conn()) as c:
        total = c.execute('SELECT COUNT(*) FROM ('+cfg['sql']+')', {'q':q}).fetchone()[0]
        pages = max(1, (total+39)//40)
        page = min(page,pages)
        result = c.execute('SELECT * FROM ('+cfg['sql']+') LIMIT :lim OFFSET :off',
                           {'q':q,'lim':40,'off':(page-1)*40}).fetchall()
    rows = [(row['id'],[_kilasi_display_archive_cell(key,row[key]) for key, _ in cfg['headers']]) for row in result]
    return render(request,'kilasi_legacy_viewer/records.html',{
        'section': 'used-stock', 'name': cfg['name'], 'kind':'table',
        'headers': [label for _,label in cfg['headers']], 'rows': rows,
        'count': total, 'page':page,'pages':pages,'q':q,
        'prev':page-1 if page>1 else None, 'next':page+1 if page<pages else None,
    })

@admin_only
def transport_trips(request):
    return records(request,'trips')

@admin_only
def transport_bookings(request):
    return records(request,'bookings')

@admin_only
def transport_expenses(request):
    return records(request,'transport-expenses')

@admin_only
def topups(request):
    return records(request,'topups')

@admin_only
def sms_history(request):
    return records(request,'sms')
