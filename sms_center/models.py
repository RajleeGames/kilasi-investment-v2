from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from core.models import Customer


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


phone_validator = RegexValidator(
    regex=r"^\d{10,15}$",
    message="Enter a valid international phone number using digits only.",
)

sender_validator = RegexValidator(
    regex=r"^[A-Za-z0-9 _.-]{3,11}$",
    message="Sender ID must be 3-11 letters/numbers/spaces.",
)


class SmsContact(TimeStampedModel):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        CUSTOMER = "customer", "System customer"
        IMPORT = "import", "CSV import"
        OTHER = "other", "Other"

    customer = models.OneToOneField(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_contact",
    )
    name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=20)
    normalized_phone = models.CharField(
        max_length=20,
        unique=True,
        validators=[phone_validator],
    )
    group_name = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    sms_allowed = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name", "normalized_phone"]

    def __str__(self):
        return self.name or self.normalized_phone


class SmsTemplate(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=80, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_templates_created",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SmsSenderProfile(TimeStampedModel):
    label = models.CharField(max_length=80, blank=True)
    sender_id = models.CharField(max_length=11, unique=True, validators=[sender_validator])
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "sender_id"]

    def __str__(self):
        return self.sender_id


class SmsCampaign(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        PARTIAL = "partial", "Partially sent"
        FAILED = "failed", "Failed"

    title = models.CharField(max_length=160, blank=True)
    sender_id = models.CharField(max_length=20)
    message = models.TextField()
    recipient_count = models.PositiveIntegerField(default=0)
    segment_count = models.PositiveIntegerField(default=1)
    estimated_units = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    provider_response = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sms_campaigns_created",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"SMS {self.pk}"

    @property
    def delivered_count(self):
        return self.recipients.filter(status=SmsRecipient.Status.DELIVERED).count()

    @property
    def failed_count(self):
        return self.recipients.filter(status=SmsRecipient.Status.FAILED).count()

    @property
    def sent_count(self):
        return self.recipients.filter(
            status__in=[SmsRecipient.Status.SENT, SmsRecipient.Status.DELIVERED]
        ).count()

    @property
    def delivery_rate(self):
        if not self.recipient_count:
            return 0
        return round((self.delivered_count / self.recipient_count) * 100, 1)


class SmsRecipient(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    campaign = models.ForeignKey(SmsCampaign, on_delete=models.CASCADE, related_name="recipients")
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_recipients",
    )
    contact = models.ForeignKey(
        SmsContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_recipients",
    )
    name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=20, validators=[phone_validator])
    personalized_message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    provider_id = models.CharField(max_length=120, blank=True)
    provider_status = models.CharField(max_length=120, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "phone"],
                name="unique_sms_phone_per_campaign",
            )
        ]

    def __str__(self):
        return f"{self.phone} - {self.status}"


class SmsImportBatch(TimeStampedModel):
    filename = models.CharField(max_length=200)
    group_name = models.CharField(max_length=100, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_imports_created",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.filename
