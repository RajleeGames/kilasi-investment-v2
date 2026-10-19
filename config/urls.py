from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("transport/", include("kilasi_transport.urls")),
    path("billing/", include("kilasi_billing.urls")),
    path("django-admin/", admin.site.urls),
    path("sms/", include("sms_center.urls")),
    path("trade/", include("kilasi_trade.urls")),  # KILASI_V2_PHASE6A_TRADE
    path("orders/", include("kilasi_orders.urls")),  # KILASI_V2_PHASE6B_ORDERS
    path("", include("core.urls")),
]


if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

# KILASI_V2_PHASE5A_SMS: explicit, independently authorized department routes.
urlpatterns.insert(0, path('retail/sms/', include(('kilasi_sms.urls', 'kilasi_sms'), namespace='retail_sms')))
urlpatterns.insert(0, path('transport/sms/', include(('kilasi_sms.urls', 'kilasi_sms'), namespace='transport_sms')))
