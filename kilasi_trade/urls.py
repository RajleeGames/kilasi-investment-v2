from django.urls import path
from . import views

app_name = 'kilasi_trade'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('genji/', views.genji_list, name='genji_list'),
    path('genji/new/', views.genji_new, name='genji_new'),
    path('genji/<int:pk>/void/', views.genji_void, name='genji_void'),
    path('topup/', views.topup_list, name='topup_list'),
    path('topup/new/', views.topup_new, name='topup_new'),
    path('used-stock/', views.used_stock_list, name='used_stock'),
    path('used-stock/<int:pk>/edit/', views.used_stock_edit, name='used_stock_edit'),
    path('used-stock/<int:pk>/sell/', views.used_sale_new, name='used_sale_new'),
]
