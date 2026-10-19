"""Retail pagination helpers for the existing Kilasi V2 views.

No data writes, no templates or route overrides, no independent CSS.
"""
from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q


def pagination_links(request, page_obj):
    """Preserve ALL GET filters (including custom ones) in pagination URLs."""
    def url(page):
        params = request.GET.copy()
        params['page'] = str(page)
        return '?' + params.urlencode()

    return {
        'kilasi_page_prev': url(page_obj.previous_page_number()) if page_obj.has_previous() else '',
        'kilasi_page_next': url(page_obj.next_page_number()) if page_obj.has_next() else '',
        'kilasi_page_first': url(1) if page_obj.has_previous() else '',
        'kilasi_page_last': url(page_obj.paginator.num_pages) if page_obj.has_next() else '',
    }


def sales_page(request, queryset):
    """Filter ONLY the queryset the original view already permissions-scoped."""
    term = (request.GET.get('q') or '').strip()[:80]
    from_input = (request.GET.get('from') or '').strip()[:10]
    to_input = (request.GET.get('to') or '').strip()[:10]

    def valid_date(value):
        if not value:
            return ''
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return ''

    date_from, date_to = valid_date(from_input), valid_date(to_input)
    if term:
        queryset = queryset.filter(
            Q(receipt_no__icontains=term)
            | Q(customer_name__icontains=term)
            | Q(customer__name__icontains=term)
        )
    if date_from:
        queryset = queryset.filter(sold_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(sold_at__date__lte=date_to)
    page_obj = Paginator(queryset.order_by('-sold_at', '-pk'), 50).get_page(request.GET.get('page'))
    extras = pagination_links(request, page_obj)
    extras.update({'q': term, 'from_day': date_from, 'to_day': date_to})
    return page_obj, extras
