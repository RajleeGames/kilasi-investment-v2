"""Read-only event audiences, derived from the actual fresh Kilasi V2 models.

Do not import the original Kilasi SQLite tables until their own modules have
been migrated. A one-time generated sales/transport cohort is *not* a customer
consent record; send approval is performed separately.
"""
from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from sms_center.services import normalize_phone

STATUS_LABELS = {
    'retail': [('','All payment methods'),('cash','Cash'),('bank','Bank'),('mobile_money','Mobile money'),('debt','Debt')],
    'transport': [('','All statuses'),('pending','Pending booking'),('confirmed','Confirmed booking'),('planned','Planned trip'),('in_progress','In progress'),('completed','Completed'),('cancelled','Cancelled')],
}

SOURCE_LABELS = {
    'retail': [('all','All available records'), ('sales','Normal sales'),
               ('proformas','Issued proformas'), ('deliveries','Delivery notes'), ('genji','Genji customers'), ('topups','Top-Up customers'), ('used_sales','Used-item customers'), ('orders','Customer orders')],
    'transport': [('all','All available records'), ('bookings','Transport bookings'),
                  ('trips','Transport trips')],
}


def parse_filters(params, department):
    today = timezone.localdate()
    def one(key, default):
        val = (params.get(key) or '').strip()
        try:
            return date.fromisoformat(val) if val else default
        except ValueError:
            raise ValidationError('Enter valid YYYY-MM-DD dates.')
    start = one('start', today - timedelta(days=30))
    end = one('end', today)
    if start > end or (end - start).days > 366 or end > today:
        raise ValidationError('Select up to 366 days ending today or earlier.')
    source = (params.get('source') or 'all').strip()
    if source not in dict(SOURCE_LABELS[department]):
        raise ValidationError('Invalid audience source.')
    query = (params.get('q') or '').strip()[:100]
    status = (params.get('status') or '').strip()
    if status not in dict(STATUS_LABELS[department]):
        raise ValidationError('Invalid status or payment method.')
    return {'start': start, 'end': end, 'source': source, 'q': query, 'status': status}


def _row(key, kind, branch, name, raw_phone, when, reference, **extra):
    phone = normalize_phone(raw_phone)
    if not phone:
        return None
    label = (name or '').strip()
    if label.casefold() in ('unknown','walk-in','customer','n/a','-'):
        label = 'Mteja'
    if hasattr(when, 'date'):
        when = timezone.localtime(when).date() if timezone.is_aware(when) else when.date()
    return {
        'key': f'{kind}:{key}', 'kind': kind, 'name': label or 'Mteja',
        'phone': phone, 'date': when, 'reference': str(reference or ''),
        'pickup': str(extra.get('pickup') or ''),
        'destination': str(extra.get('destination') or ''),
        'amount': str(extra.get('amount') or ''),
        'status': str(extra.get('status') or ''),
        'branch': str(branch.name) if branch else 'HAMA NA KILASI',
    }


def audience_rows(department, branch, filters):
    start,end,source = filters['start'],filters['end'],filters['source']
    rows = []
    if department == 'retail':
        from core.models import Sale
        from kilasi_billing.models import Proforma, DeliveryNote
        if source in ('all','sales'):
            qs = Sale.objects.filter(branch=branch, status='completed',
                 sold_at__date__range=(start,end), customer__isnull=False)
            for sale in qs.select_related('customer').order_by('-sold_at','-pk')[:10000]:
                r = _row(sale.pk, 'sales', branch, sale.customer.name, sale.customer.phone,
                         sale.sold_at, sale.receipt_no, amount=sale.grand_total,
                         status=sale.payment_method)
                if r: rows.append(r)
        if source in ('all','proformas'):
            qs = Proforma.objects.filter(branch=branch, status='issued', issue_date__range=(start,end))
            for obj in qs.order_by('-issue_date','-pk')[:10000]:
                r = _row(obj.pk, 'proformas', branch, obj.customer_name, obj.customer_phone,
                         obj.issue_date, obj.number, status=obj.status)
                if r: rows.append(r)
        if source in ('all','deliveries'):
            qs = DeliveryNote.objects.filter(sale__branch=branch, sale__status='completed',
                  issue_date__range=(start,end), sale__customer__isnull=False)
            for obj in qs.select_related('sale__customer').order_by('-issue_date','-pk')[:10000]:
                r = _row(obj.pk, 'deliveries', branch, obj.sale.customer.name,
                         obj.sale.customer.phone, obj.issue_date, obj.number, status='delivery')
                if r: rows.append(r)
        # KILASI_V2_PHASE6A_TRADE — new distinct sale/exchange customer sources.
        # These records do not automatically authorize marketing SMS.
        from kilasi_trade.models import GenjiSale, TopupTransaction, UsedTopupSale
        if source in ('all','genji'):
            qs=GenjiSale.objects.filter(branch=branch,status='completed',sell_date__range=(start,end))
            for obj in qs.order_by('-sell_date','-pk')[:10000]:
                r=_row(obj.pk,'genji',branch,obj.customer_name,obj.customer_phone,
                       obj.sell_date,f'GENJI-{obj.pk:06d}',amount=obj.total_selling,status='completed')
                if r:rows.append(r)
        if source in ('all','topups'):
            qs=TopupTransaction.objects.filter(branch=branch,status='completed',created_at__date__range=(start,end)).select_related('customer')
            for obj in qs.order_by('-created_at','-pk')[:10000]:
                r=_row(obj.pk,'topups',branch,obj.customer_name or (obj.customer.name if obj.customer else ''),obj.customer_phone or (obj.customer.phone if obj.customer else ''),
                       obj.created_at,f'TOPUP-{obj.pk:06d}',amount=obj.addon_amount,status='completed')
                if r:rows.append(r)
        if source in ('all','used_sales'):
            qs=UsedTopupSale.objects.filter(branch=branch,created_at__date__range=(start,end)).select_related('customer')
            for obj in qs.order_by('-created_at','-pk')[:10000]:
                r=_row(obj.pk,'used_sales',branch,obj.customer_name or (obj.customer.name if obj.customer else ''),obj.customer_phone or (obj.customer.phone if obj.customer else ''),
                       obj.created_at,f'USED-{obj.pk:06d}',amount=obj.sold_price,status='completed')
                if r:rows.append(r)
        # KILASI_V2_PHASE6B_ORDERS: separate Retail customer-order SMS audience.
        if source in ('all','orders'):
            from kilasi_orders.models import CustomerOrder
            orders_qs=CustomerOrder.objects.filter(branch=branch,created_at__date__range=(start,end))
            for order in orders_qs.order_by('-created_at','-pk')[:10000]:
                r=_row(order.pk,'orders',branch,order.customer_name,order.customer_phone,
                    order.created_at,order.order_no,amount=order.total_amount,status=order.order_status)
                if r:rows.append(r)
    elif department == 'transport':
        from kilasi_transport.models import Booking, Trip
        if source in ('all','bookings'):
            qs = Booking.objects.filter(requested_date__range=(start,end))
            for obj in qs.order_by('-requested_date','-pk')[:10000]:
                r = _row(obj.pk, 'bookings', branch, obj.customer_name, obj.customer_phone,
                         obj.requested_date, obj.reference, pickup=obj.origin,
                         destination=obj.destination, amount=obj.quoted_amount, status=obj.status)
                if r: rows.append(r)
        if source in ('all','trips'):
            qs = Trip.objects.filter(scheduled_start__date__range=(start,end))
            for obj in qs.order_by('-scheduled_start','-pk')[:10000]:
                r = _row(obj.pk, 'trips', branch, obj.customer_name, obj.customer_phone,
                         obj.scheduled_start, obj.reference, pickup=obj.origin,
                         destination=obj.destination, amount=obj.agreed_fee, status=obj.status)
                if r: rows.append(r)
    if filters.get('status'):
        rows=[r for r in rows if r['status']==filters['status']]
    term = filters['q'].casefold()
    if term:
        rows = [r for r in rows if term in (r['name']+' '+r['phone']+' '+r['reference']).casefold()]
    # Keep one row per business event. Deduplicate phone only when drafting SMS.
    return sorted(rows, key=lambda r: (r['date'],r['reference']), reverse=True)
