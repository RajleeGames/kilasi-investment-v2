"""Department-scoped send records that reuse Novman Beem campaigns & DLRs."""
import uuid
from django.conf import settings
from django.db import models


class KilasiSmsDispatch(models.Model):
    DEPARTMENTS = [('retail', 'Retail'), ('transport', 'Transport')]
    STATES = [('draft', 'Awaiting approval'), ('sending', 'Sending / review required'),
              ('sent', 'Accepted by provider'), ('partial', 'Partially accepted'),
              ('failed', 'Not accepted')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='kilasi_sms_dispatches', null=True, blank=True)
    department = models.CharField(max_length=16, choices=DEPARTMENTS)
    source = models.CharField(max_length=24)
    campaign = models.OneToOneField('sms_center.SmsCampaign', on_delete=models.PROTECT,
                                    related_name='kilasi_dispatch')
    status = models.CharField(max_length=16, choices=STATES, default='draft')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='kilasi_sms_dispatches')
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['department', 'branch', 'created_at'])]

    def __str__(self):
        return f'{self.get_department_display()} SMS {str(self.id)[:8]}'


# KILASI_V2_PHASE5B_AUTOMATION: event models, original 5A model remains intact.
from .automation_definitions import EVENTS
from django.utils import timezone
class KilasiSmsAutomationRule(models.Model):
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='sms_automation_rules', null=True, blank=True)
    department = models.CharField(max_length=16, choices=KilasiSmsDispatch.DEPARTMENTS)
    event_key = models.CharField(max_length=80, choices=[(e[0], e[1]) for e in EVENTS])
    template = models.ForeignKey('sms_center.SmsTemplate', on_delete=models.PROTECT, related_name='kilasi_automation_rules')
    sender_id = models.CharField(max_length=11, blank=True)
    enabled = models.BooleanField(default=False)
    enabled_at = models.DateTimeField(null=True, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'event_key']
        constraints = [models.UniqueConstraint(fields=['branch', 'event_key'], name='ki_sms_rule_branch_event_5b'),
                       models.UniqueConstraint(fields=['event_key'], condition=models.Q(branch__isnull=True), name='ki_hama_rule_global_v7')]

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        matched = next((e for e in EVENTS if e[0] == self.event_key), None)
        if not matched or matched[2] != self.department:
            raise ValidationError('Event must match its SMS department.')
        if self.enabled and (not self.sender_id or not self.template_id):
            raise ValidationError('Choose a template and a Beem-approved sender before enabling.')

    def __str__(self):
        return f'{self.branch or "HAMA NA KILASI"}: {self.get_event_key_display()}'


class KilasiSmsAutomaticJob(models.Model):
    STATES = [
        ('queued', 'Queued'), ('sending', 'Sending — review before retry'),
        ('accepted', 'Accepted by Beem'), ('blocked', 'Opted out / ineligible'),
        ('unknown', 'Failed or uncertain — no auto retry'), ('cancelled', 'Cancelled'),
    ]
    rule = models.ForeignKey(KilasiSmsAutomationRule, on_delete=models.PROTECT, related_name='jobs')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, null=True, blank=True)
    event_key = models.CharField(max_length=80)
    source_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=20)
    body = models.TextField()
    due_at = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    campaign = models.OneToOneField('sms_center.SmsCampaign', on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATES, default='queued', db_index=True)
    error_text = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['branch', 'event_key', 'source_id'], name='kilasi_sms_auto_event_once_5b'),
                       models.UniqueConstraint(fields=['event_key','source_id'], condition=models.Q(branch__isnull=True), name='ki_hama_job_once_v7')]
        indexes = [models.Index(fields=['branch', 'status', 'due_at'], name='kilasi_sms_due_5b')]


class KilasiDeliveryConfirmation(models.Model):
    """Actual delivery acknowledgment, not inferred from delivery-note creation."""
    note = models.OneToOneField('kilasi_billing.DeliveryNote', on_delete=models.PROTECT,
                                related_name='kilasi_sms_delivery_confirmation')
    received_by = models.CharField(max_length=160)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    confirmed_at = models.DateTimeField(default=timezone.now)
    note_text = models.CharField(max_length=250, blank=True)
