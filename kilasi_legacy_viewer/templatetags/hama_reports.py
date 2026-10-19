from django import template
from kilasi_legacy_viewer.hama_final_views import _filters, historical_summary

register=template.Library()

@register.inclusion_tag('kilasi_legacy_viewer/hama_dashboard_panel.html',takes_context=True)
def hama_dashboard_panel(context):
    req=context['request']
    if not req.user.is_authenticated or not req.user.is_superuser:
        return {'hama_hidden':True}
    start,end=_filters(req)
    return {'hama':historical_summary(None,start,end),'request':req}
