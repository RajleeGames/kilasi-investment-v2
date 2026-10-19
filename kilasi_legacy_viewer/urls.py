from django.urls import path
from . import views
app_name = 'kilasi_legacy_viewer'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('<slug:section>/', views.records, name='records'),
    path('invoice/<int:invoice_id>/lines/', views.invoice_lines, name='invoice_lines'),
]
