"""Local staging-only historical HAMA closeout page. Superuser, GET only, source intact."""
from contextlib import closing
from decimal import Decimal
from django.http import Http404
from django.shortcuts import render
from .views import hama_history_only, archival_conn

STATUS_CHOICES = ('', 'completed', 'in_transit', 'pending', 'cancelled')

@hama_history_only
def closed_trips(request):
    if request.method != 'GET':
        raise Http404('Read-only historical register')
    q = request.GET.get('q', '').strip()[:80]
    status = request.GET.get('status', '').strip()
    if status not in STATUS_CHOICES:
        status = ''
    try:
        page = min(max(int(request.GET.get('page', '1')), 1), 100000)
    except (TypeError, ValueError):
        page = 1
    conditions = ["(:status='' OR c.historical_status=:status)",
                  "(:q='' OR CAST(c.legacy_trip_id AS TEXT)=:q OR t.customer_name LIKE :likeq OR t.origin LIKE :likeq OR t.destination LIKE :likeq OR v.plate_number LIKE :likeq)"]
    joined = ''' FROM kilasi_stage7h_transport_closure c
       JOIN kilasi_legacy_transport_trip t ON t.id=c.legacy_trip_id
       LEFT JOIN kilasi_legacy_transport_vehicle v ON v.id=t.vehicle_id
       LEFT JOIN kilasi_legacy_transport_driver d ON d.id=t.driver_id
       WHERE '''+' AND '.join(conditions)
    args = {'status': status, 'q': q, 'likeq': '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'}
    # Treat search text literally (prevent '%' and '_' wildcard expansion).
    joined = joined.replace('t.customer_name LIKE :likeq', "t.customer_name LIKE :likeq ESCAPE '\\'")
    joined = joined.replace('t.origin LIKE :likeq', "t.origin LIKE :likeq ESCAPE '\\'")
    joined = joined.replace('t.destination LIKE :likeq', "t.destination LIKE :likeq ESCAPE '\\'")
    joined = joined.replace('v.plate_number LIKE :likeq', "v.plate_number LIKE :likeq ESCAPE '\\'")
    with closing(archival_conn()) as c:
        t={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'kilasi_stage7h_transport_closure' not in t:
            raise Http404('Stage 7H closure is not available in this staging database')
        manifest = c.execute('SELECT owner_attested_hama_closed,no_transport_ar_carryforward FROM kilasi_stage7h_manifest WHERE id=1').fetchone()
        if not manifest or tuple(manifest)!=(1,1):
            raise Http404('Owner closeout is not validated')
        total = c.execute('SELECT COUNT(*) ' + joined, args).fetchone()[0]
        pages=max(1,(total+39)//40);page=min(page,pages)
        rows=c.execute('''SELECT c.legacy_trip_id AS id, t.trip_date AS date,
           COALESCE(t.customer_name,'—') AS customer,
           COALESCE(t.origin,'—') AS origin, COALESCE(t.destination,'—') AS destination,
           COALESCE(v.plate_number,'—') AS vehicle,
           COALESCE(TRIM(COALESCE(d.first_name,'')||' '||COALESCE(d.last_name,'')),'—') AS driver,
           c.historical_status AS old_status,c.business_period_disposition AS disposition,
           t.amount_charged AS original_charge
           ''' + joined + ' ORDER BY c.legacy_trip_id DESC LIMIT :limit OFFSET :offset',
           {**args,'limit':40,'offset':(page-1)*40}).fetchall()
        source_statuses={row[0]:row[1] for row in c.execute('SELECT historical_status,COUNT(*) FROM kilasi_stage7h_transport_closure GROUP BY historical_status')}
        summary = c.execute('''SELECT (SELECT COUNT(*) FROM kilasi_stage7h_transport_closure),
            (SELECT COUNT(*) FROM kilasi_stage7h_booking_closure),
            (SELECT COUNT(*) FROM kilasi_stage7g_expense_review)''').fetchone()
    return render(request,'kilasi_legacy_viewer/hama_closed_history.html',{
        'rows':rows, 'total':total, 'page':page,'pages':pages,
        'prev':page-1 if page>1 else None,
        'next':page+1 if page<pages else None,
        'q':q,'status':status,'choices':STATUS_CHOICES[1:],
        'stats':{'trips':summary[0],'bookings':summary[1],'expenses':summary[2]},
        'statuses':source_statuses,
    })
