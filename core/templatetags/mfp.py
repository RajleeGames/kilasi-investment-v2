from decimal import Decimal
from django import template
register=template.Library()
@register.filter
def money(value):
    try: return f'{Decimal(value):,.0f}'
    except Exception: return '0'
@register.filter
def qty(value):
    try:
        d=Decimal(value)
        if d==d.to_integral(): return f'{int(d):,}'
        return f'{d.normalize():f}'.rstrip('0').rstrip('.')
    except Exception: return value
