from django.urls import path
from . import views

app_name='kilasi_sms'
from . import automation_views
urlpatterns=[
    # KILASI_V2_PHASE5B_AUTOMATION
    path('automations/',automation_views.automations,name='automations'),
    path('automations/retry/<int:pk>/',automation_views.retry_transport_sms,name='retry_transport_sms'),
    path('delivery/<int:pk>/mark-delivered/',automation_views.mark_delivered,name='mark_delivered'),
    path('',views.hub,name='hub'),
    path('draft/',views.create_draft,name='draft'),
    path('history/',views.history,name='history'),
    path('drafts/<uuid:pk>/',views.detail,name='detail'),
    path('drafts/<uuid:pk>/confirm/',views.confirm,name='confirm'),
]
