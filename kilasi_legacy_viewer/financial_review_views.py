"""Phase7G staging-only review screen. Uses the same global Kilasi V2 CSS."""
from contextlib import closing
from django.http import Http404
from django.shortcuts import render
from .views import admin_only, archival_conn


@admin_only
def closeout(request):
    with closing(archival_conn()) as c:
        required = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kilasi_stage7g_%'")}
        if 'kilasi_stage7g_manifest' not in required:
            raise Http404('Launch financial review requires a Phase7G staging database.')
        status = c.execute('SELECT phase,import_approved_for_launch,transport_settlements_verified,topup_cogs_verified FROM kilasi_stage7g_manifest WHERE id=1').fetchone()
        if not status or status['phase']!='7G.1' or status['import_approved_for_launch']!=0:
            raise Http404('Unexpected migration status; review stopped for safety.')
        trip_totals = c.execute("SELECT COUNT(*) AS count,COALESCE(SUM(CAST(amount_charged AS REAL)),0) AS charge FROM kilasi_stage7g_trip_review").fetchone()
        trips_status = c.execute('SELECT old_status,COUNT(*) AS n FROM kilasi_stage7g_trip_review GROUP BY old_status ORDER BY old_status').fetchall()
        expenses = c.execute('SELECT COUNT(*) AS count,COALESCE(SUM(CAST(recorded_cost AS REAL)),0) AS amount FROM kilasi_stage7g_expense_review').fetchone()
        bookings = c.execute('SELECT COUNT(*) AS n FROM kilasi_stage7g_booking_review').fetchone()[0]
        topup = c.execute('SELECT COUNT(*) AS n,COALESCE(SUM(CAST(cash_addon AS REAL)),0) AS cash,COALESCE(SUM(CAST(total_consideration AS REAL)),0) AS consideration FROM kilasi_stage7g_topup_review').fetchone()
        available = c.execute("SELECT COUNT(*) FROM kilasi_stage7g_used_item_review WHERE original_status='available'").fetchone()[0]
        used_sale = c.execute('SELECT COALESCE(SUM(CAST(realized_used_sale_profit AS REAL)),0) FROM kilasi_stage7g_used_item_review').fetchone()[0]
        debt = c.execute("SELECT COALESCE(SUM(CAST(grand_total AS REAL)),0) FROM core_sale WHERE status='completed' AND payment_method='debt'").fetchone()[0] - c.execute('SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM core_customerpayment WHERE is_voided=0').fetchone()[0]
        trip_rows = c.execute('SELECT legacy_id,service_date,old_status,amount_charged,settlement_classification FROM kilasi_stage7g_trip_review ORDER BY legacy_id DESC LIMIT 15').fetchall()
        topup_rows = c.execute('SELECT legacy_id,event_at,used_item_value,cash_addon,total_consideration,financial_classification FROM kilasi_stage7g_topup_review ORDER BY legacy_id DESC').fetchall()
        return render(request, 'kilasi_legacy_viewer/financial_closeout.html', {
            'status':status,'trip_totals':trip_totals,'trips_status':trips_status,
            'expenses':expenses,'bookings':bookings,'topup':topup,'available':available,
            'used_sale':used_sale,'debt':debt,'trip_rows':trip_rows,'topup_rows':topup_rows,
        })
