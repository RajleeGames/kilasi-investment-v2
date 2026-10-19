from django.urls import path
from . import views

app_name='kilasi_orders'
urlpatterns=[
    path('',views.orders,name='orders'),
    path('new/',views.order_new,name='new'),
    path('<int:pk>/',views.order_detail,name='order_detail'),
    path('<int:pk>/pay/',views.order_pay,name='pay'),
    path('<int:pk>/status/',views.order_status,name='status'),
    path('payments/<int:pk>/void/',views.order_payment_void,name='payment_void'),
    path('returns/',views.returns,name='returns'),
    path('returns/new/',views.return_new,name='return_new'),
    path('returns/<int:pk>/',views.return_detail,name='return_detail'),
]
