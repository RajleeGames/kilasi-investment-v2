from django.urls import path

from . import views

app_name = "sms_center"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("send/", views.send_sms, name="send"),
    path("balance/", views.balance_json, name="balance"),

    path("contacts/", views.contacts, name="contacts"),
    path("contacts/new/", views.contact_create, name="contact_create"),
    path("contacts/<int:pk>/edit/", views.contact_edit, name="contact_edit"),
    path("contacts/<int:pk>/delete/", views.contact_delete, name="contact_delete"),
    path("contacts/sync-customers/", views.sync_customers, name="sync_customers"),
    path("contacts/import/", views.import_contacts, name="import_contacts"),
    path("contacts/sample.csv", views.sample_csv, name="sample_csv"),

    path("templates/", views.templates, name="templates"),
    path("templates/new/", views.template_create, name="template_create"),
    path("templates/<int:pk>/edit/", views.template_edit, name="template_edit"),
    path("templates/<int:pk>/delete/", views.template_delete, name="template_delete"),

    path("senders/", views.senders, name="senders"),
    path("senders/new/", views.sender_create, name="sender_create"),
    path("senders/<int:pk>/edit/", views.sender_edit, name="sender_edit"),
    path("senders/<int:pk>/delete/", views.sender_delete, name="sender_delete"),

    path("history/", views.history, name="history"),
    path("history/<int:pk>/", views.campaign_detail, name="campaign_detail"),

    path("dlr/", views.delivery_report_webhook, name="delivery_report_webhook"),
]
