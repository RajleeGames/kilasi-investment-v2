"""Reusable HAMA NA KILASI list filters. No writes or global state."""
from datetime import date
from django.core.paginator import Paginator
from django.db.models import Q


def _parse_date(value):
    text = (value or '').strip()
    if not text:
        return None, ''
    try:
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text or not 2000 <= parsed.year <= 2100:
            raise ValueError('Invalid date')
        return parsed, ''
    except (ValueError, TypeError):
        return None, 'Enter a valid date between 2000 and 2100.'


def filtered_page(request, queryset, *, date_field, search_fields=(), statuses=(),
                  state_field='status', category_choices=(), page_size=25, vehicle_field=None, driver_field=None):
    q = (request.GET.get('q') or '').strip()[:100]
    selected = (request.GET.get('status') or '').strip()
    date_from, from_error = _parse_date(request.GET.get('start'))
    date_to, to_error = _parse_date(request.GET.get('end'))
    errors = [x for x in (from_error, to_error) if x]

    if q and search_fields:
        conditions = Q()
        for field in search_fields:
            conditions |= Q(**{field + '__icontains': q})
        queryset = queryset.filter(conditions)

    category = (request.GET.get('category') or '').strip()
    if category_choices and category in dict(category_choices):
        queryset = queryset.filter(category=category)
    else:
        category = ''

    choices = dict(statuses)
    if selected in choices:
        if state_field == 'is_voided':
            queryset = queryset.filter(is_voided=(selected == 'voided'))
        else:
            queryset = queryset.filter(**{state_field: selected})
    else:
        selected = ''

    # Invalid inputs do not become raw SQL or server errors.
    if date_from and date_to and date_from > date_to:
        errors.append('From date must be on or before To date.')
    elif not errors:
        if date_from:
            queryset = queryset.filter(**{date_field + '__gte': date_from})
        if date_to:
            queryset = queryset.filter(**{date_field + '__lte': date_to})

    vehicle_id=(request.GET.get('vehicle') or '').strip()
    driver_id=(request.GET.get('driver') or '').strip()
    # IDs are validated against the DB; invalid values never influence SQL fields.
    if vehicle_field and vehicle_id.isdecimal() and len(vehicle_id)<12:
        queryset=queryset.filter(**{vehicle_field:int(vehicle_id)})
    else: vehicle_id=''
    if driver_field and driver_id.isdecimal() and len(driver_id)<12:
        queryset=queryset.filter(**{driver_field:int(driver_id)})
    else: driver_id=''

    pager = Paginator(queryset, page_size)
    page_obj = pager.get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    encoded = params.urlencode()
    return {
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'page_query': encoded + '&' if encoded else '',
        'total_rows': pager.count,
        'q': q,
        'status': selected,
        'start': request.GET.get('start', '')[:10],
        'end': request.GET.get('end', '')[:10],
        'filter_errors': errors,
        'status_choices': statuses,
        'category': category,
        'category_choices': category_choices,
        'selected_vehicle': vehicle_id, 'selected_driver': driver_id,
    }
