from django.urls import path
from . import views, access_views, driver_views

app_name = 'transport'

urlpatterns = [
    # KILASI_V2_PHASE4B_ACCESS
    path('users/', access_views.users, name='users'),
    path('users/<int:pk>/edit/', access_views.user_edit, name='user_edit'),
    # Driver-only routes. Manager and Retail views retain their existing URLs.
    path('driver/', driver_views.home, name='driver_home'),
    path('driver/trips/new/', driver_views.trip_new, name='driver_trip_new'),
    path('driver/trips/<int:pk>/', driver_views.trip_detail, name='driver_trip_detail'),
    path('driver/trips/<int:pk>/edit/', driver_views.trip_edit, name='driver_trip_edit'),
    path('driver/trips/<int:pk>/status/', driver_views.trip_status, name='driver_trip_status'),
    path('', views.dashboard, name='home'),
    path('vehicles/', views.vehicles, name='vehicles'),
    path('vehicles/new/', views.vehicle_edit, name='vehicle_new'),
    path('vehicles/<int:pk>/edit/', views.vehicle_edit, name='vehicle_edit'),
    path('drivers/', views.drivers, name='drivers'),
    path('drivers/new/', views.driver_edit, name='driver_new'),
    path('drivers/<int:pk>/edit/', views.driver_edit, name='driver_edit'),
    path('trips/', views.trips, name='trips'),
    path('trips/new/', views.trip_edit, name='trip_new'),
    path('trips/<int:pk>/', views.trip_detail, name='trip_detail'),
    path('trips/<int:pk>/edit/', views.trip_edit, name='trip_edit'),
    path('trips/<int:pk>/status/', views.trip_status, name='trip_status'),
    path('trips/<int:pk>/payments/', views.payment_new, name='payment_new'),
    path('payments/<int:pk>/void/', views.payment_void, name='payment_void'),
    path('bookings/', views.bookings, name='bookings'),
    path('bookings/new/', views.booking_edit, name='booking_new'),
    path('bookings/<int:pk>/edit/', views.booking_edit, name='booking_edit'),
    path('bookings/<int:pk>/status/', views.booking_status, name='booking_status'),
    path('expenses/', views.expenses, name='expenses'),
    path('expenses/<int:pk>/void/', views.expense_void, name='expense_void'),
    path('reports/', views.reports, name='reports'),
]
