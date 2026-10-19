"""Historical HAMA summaries. Read only, no finance writes or legacy SMS replay."""
from contextlib import closing
from datetime import date
from decimal import Decimal
import re
from django.http import Http404
from django.shortcuts import render
from .views import archival_conn, admin_only

KINDS = {'trips', 'bookings', 'expenses'}
PER_PAGE = 35


def _date(value):
    if not value:
        return ''
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        return ''
    return parsed.isoformat() if 2000 <= parsed.year <= 2100 else ''


def _filters(request, prefix='history_'):
    start = _date(request.GET.get(prefix+'from', ''))
    end = _date(request.GET.get(prefix+'to', ''))
    if start and end and start > end:
        start, end = end, start
    return start, end


def _datesql(col):
    return f"(:start='' OR substr({col},1,10)>=:start) AND (:end='' OR substr({col},1,10)<=:end)"


def historical_summary(db, start='', end=''):
    """Use a read-only SQLite connection, closure manifest and parameters.

    Revenue semantics: charges historically recorded; NOT settled cash or current AR.
    """
    q = {'start': start, 'end': end}
    with closing(archival_conn()) as c:
        closure = c.execute('SELECT owner_attested_hama_closed,no_transport_ar_carryforward FROM kilasi_stage7h_manifest WHERE id=1').fetchone()
        if not closure or tuple(closure)!=(1,1):
            raise ValueError('HAMA historical closeout is not verified')
        trip = c.execute('''SELECT COUNT(*) AS n,
            COALESCE(SUM(CASE WHEN status!='cancelled' THEN CAST(amount_charged AS NUMERIC) ELSE 0 END),0) AS charged
            FROM kilasi_legacy_transport_trip WHERE ''' + _datesql('trip_date'), q).fetchone()
        booking = c.execute('SELECT COUNT(*) FROM kilasi_legacy_transport_booking WHERE '+_datesql('booking_datetime'),q).fetchone()[0]
        expense = c.execute('''SELECT COUNT(*) AS n,COALESCE(SUM(CAST(amount AS NUMERIC)),0) AS spent
            FROM kilasi_legacy_transport_transportexpense WHERE '''+_datesql('expense_date'),q).fetchone()
        statuses = {row['status']:row['n'] for row in c.execute('''SELECT status,COUNT(*) AS n
            FROM kilasi_legacy_transport_trip WHERE '''+_datesql('trip_date')+' GROUP BY status',q)}
        fleet = c.execute('SELECT COUNT(*) FROM kilasi_legacy_transport_vehicle').fetchone()[0]
        drivers = c.execute('SELECT COUNT(*) FROM kilasi_legacy_transport_driver').fetchone()[0]
    return {'trips':trip['n'], 'charges':Decimal(str(trip['charged'])),
            'bookings':booking, 'expenses':expense['n'], 'spent':Decimal(str(expense['spent'])),
            'statuses':statuses,'fleet':fleet,'drivers':drivers,'start':start,'end':end}


def _query(request, kind):
    start, end = _filters(request, '') # list uses from and to
    q = (request.GET.get('q') or '').strip()[:80]
    status = (request.GET.get('status') or '').strip()[:30]
    if status and not re.fullmatch(r'[a-z_]{1,30}',status):
        status = ''
    try:
        page = max(1, min(1000000, int(request.GET.get('page','1'))))
    except (ValueError,TypeError):
        page = 1
    return {'start':start,'end':end,'q':q,'status':status,'page':page}


@admin_only
def history_records(request, kind):
    if request.method != 'GET' or kind not in KINDS:
        raise Http404('Archived HAMA history is read-only')
    f = _query(request,kind)
    text = f['q']
    like = '%' + text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    args = {'start':f['start'],'end':f['end'],'q':text,'like':like,'status':f['status']}
    if kind == 'trips':
        source = '''FROM kilasi_stage7h_transport_closure cl
            JOIN kilasi_legacy_transport_trip t ON t.id=cl.legacy_trip_id
            LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=t.vehicle_id'''
        conditions = [_datesql('t.trip_date'),"(:status='' OR t.status=:status)",
            "(:q='' OR CAST(t.id AS TEXT)=:q OR t.origin LIKE :like ESCAPE '\\' OR t.destination LIKE :like ESCAPE '\\' OR t.customer_name LIKE :like ESCAPE '\\' OR v.plate_number LIKE :like ESCAPE '\\')"]
        selected = '''t.id AS id,t.trip_date AS event_date,t.customer_name AS customer,t.origin AS origin,
            t.destination AS destination,COALESCE(v.plate_number,'—') AS vehicle,
            t.status AS status,t.amount_charged AS amount,cl.business_period_disposition AS closed_status'''
        date_label='Trip date'
        title='Closed trips'
    elif kind == 'bookings':
        source = '''FROM kilasi_stage7h_booking_closure cl
            JOIN kilasi_legacy_transport_booking t ON t.id=cl.legacy_booking_id'''
        conditions = [_datesql('t.booking_datetime'),"(:status='' OR t.status=:status)",
            "(:q='' OR CAST(t.id AS TEXT)=:q OR t.customer_name LIKE :like ESCAPE '\\' OR t.pickup_area LIKE :like ESCAPE '\\' OR t.destination LIKE :like ESCAPE '\\')"]
        selected = '''t.id AS id,t.booking_datetime AS event_date,t.customer_name AS customer,
            t.pickup_area AS origin,t.destination AS destination,'—' AS vehicle,
            t.status AS status,0 AS amount,'CLOSED_ARCHIVE' AS closed_status'''
        date_label='Booking date'
        title='Closed bookings'
    else:
        source = '''FROM kilasi_stage7g_expense_review e
            LEFT JOIN kilasi_legacy_transport_transportexpense t ON t.id=e.legacy_id
            LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=t.vehicle_id'''
        conditions = [_datesql('t.expense_date'),
            "(:q='' OR CAST(t.id AS TEXT)=:q OR t.description LIKE :like ESCAPE '\\' OR t.expense_type LIKE :like ESCAPE '\\' OR v.plate_number LIKE :like ESCAPE '\\')"]
        selected = '''t.id AS id,t.expense_date AS event_date,t.description AS customer,
            t.expense_type AS origin,'—' AS destination,COALESCE(v.plate_number,'—') AS vehicle,
            'archived' AS status,t.amount AS amount,'CLOSED_ARCHIVE' AS closed_status'''
        date_label='Expense date'
        title='Historical expenses'
        f['status']=''; args['status']=''
    where = ' AND '.join(conditions)
    with closing(archival_conn()) as c:
        closure = c.execute('SELECT owner_attested_hama_closed,no_transport_ar_carryforward FROM kilasi_stage7h_manifest WHERE id=1').fetchone()
        if tuple(closure or ())!=(1,1):
            raise Http404('HAMA closeout approval missing')
        count = c.execute('SELECT COUNT(*) '+source+' WHERE '+where,args).fetchone()[0]
        pages=max(1,(count+PER_PAGE-1)//PER_PAGE)
        page=min(f['page'],pages)
        rows=c.execute('SELECT '+selected+' '+source+' WHERE '+where+' ORDER BY t.id DESC LIMIT :limit OFFSET :offset',
                       {**args,'limit':PER_PAGE,'offset':(page-1)*PER_PAGE}).fetchall()
        amounts=c.execute('SELECT COALESCE(SUM(CAST(z.amount AS NUMERIC)),0) FROM (SELECT '+selected+' '+source+' WHERE '+where+') z',args).fetchone()[0]
    return render(request,'kilasi_legacy_viewer/hama_paginated.html', {
        **f,'page':page,'pages':pages,'total':count,'rows':rows,'kind':kind,
        'title':title,'date_label':date_label,'total_amount':amounts,
        'prev':page-1 if page>1 else None,'next':page+1 if page<pages else None,
        'status_choices':('completed','in_transit','pending','cancelled') if kind=='trips' else ('completed','pending','cancelled') if kind=='bookings' else (),
    })
