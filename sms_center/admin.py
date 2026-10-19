from django.contrib import admin

from .models import (
    SmsCampaign,
    SmsContact,
    SmsImportBatch,
    SmsRecipient,
    SmsSenderProfile,
    SmsTemplate,
)


@admin.register(SmsContact)
class SmsContactAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_phone", "group_name", "source", "sms_allowed", "is_active")
    list_filter = ("source", "sms_allowed", "is_active")
    search_fields = ("name", "phone", "normalized_phone", "group_name")


@admin.register(SmsTemplate)
class SmsTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active", "updated_at")
    list_filter = ("is_active", "category")
    search_fields = ("name", "body")


@admin.register(SmsSenderProfile)
class SmsSenderProfileAdmin(admin.ModelAdmin):
    list_display = ("sender_id", "label", "is_default", "is_active")
    list_filter = ("is_default", "is_active")


class SmsRecipientInline(admin.TabularInline):
    model = SmsRecipient
    extra = 0
    readonly_fields = ("phone", "name", "status", "provider_status", "sent_at", "delivered_at")


@admin.register(SmsCampaign)
class SmsCampaignAdmin(admin.ModelAdmin):
    list_display = ("title", "sender_id", "recipient_count", "estimated_units", "status", "created_at")
    list_filter = ("status", "sender_id")
    search_fields = ("title", "message", "sender_id")
    inlines = [SmsRecipientInline]


@admin.register(SmsImportBatch)
class SmsImportBatchAdmin(admin.ModelAdmin):
    list_display = ("filename", "total_rows", "imported_count", "updated_count", "error_count", "created_at")


admin.site.register(SmsRecipient)
