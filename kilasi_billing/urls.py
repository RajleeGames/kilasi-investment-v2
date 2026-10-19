from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('', views.home, name='home'),
    path('proformas/', views.proforma_list, name='proformas'),
    path('proformas/new/', views.proforma_edit, name='proforma_new'),
    path('proformas/<int:pk>/', views.proforma_detail, name='proforma_detail'),
    path('proformas/<int:pk>/edit/', views.proforma_edit, name='proforma_edit'),
    path('proformas/<int:pk>/status/', views.proforma_status, name='proforma_status'),
    path('invoices/', views.invoice_list, name='invoices'),
    path('invoices/new/<int:sale_id>/', views.invoice_new, name='invoice_new'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/pay/', views.invoice_pay, name='invoice_pay'),
    path('delivery-notes/', views.delivery_list, name='deliveries'),
    path('delivery-notes/new/<int:sale_id>/', views.delivery_new, name='delivery_new'),
    path('delivery-notes/<int:pk>/', views.delivery_detail, name='delivery_detail'),
]
