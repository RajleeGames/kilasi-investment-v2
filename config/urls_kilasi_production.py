"""Normal operational V2 routes + private read-only historical screens."""
from django.urls import path,include
from .urls import urlpatterns as normal_urls
from kilasi_legacy_viewer import native_history_views
from kilasi_legacy_viewer.financial_review_views import closeout
from kilasi_legacy_viewer.hama_closed_history import closed_trips
urlpatterns=[
    path('legacy/',include('kilasi_legacy_viewer.urls')),
    path('transport/closed-history/',closed_trips,name='hama_closed_history'),
    path('migration/reconcile/',closeout,name='stage7g_reconcile'),
    path('sms/history/',native_history_views.sms_history,name='legacy_sms_history'),
    path('transport/sms/history/',native_history_views.sms_history,name='legacy_hama_sms_history'),
]+list(normal_urls)

# KILASI_PHASE9D_HAMA_HISTORY_ROUTES
from kilasi_legacy_viewer.hama_final_views import history_records as hama_history_records
urlpatterns = [
    path('transport/history/<str:kind>/',hama_history_records,name='hama_history_filtered'),
] + list(urlpatterns)
