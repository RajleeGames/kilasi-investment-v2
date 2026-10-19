from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0010_alter_expense_category"),
    ]

    operations = [
        migrations.CreateModel(
            name="SmsSenderProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("label", models.CharField(blank=True, max_length=80)),
                ("sender_id", models.CharField(max_length=11, unique=True, validators=[django.core.validators.RegexValidator(message="Sender ID must be 3-11 letters/numbers/spaces.", regex="^[A-Za-z0-9 _.-]{3,11}$")])),
                ("is_default", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["-is_default", "sender_id"]},
        ),
        migrations.CreateModel(
            name="SmsContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(blank=True, max_length=160)),
                ("phone", models.CharField(max_length=20)),
                ("normalized_phone", models.CharField(max_length=20, unique=True, validators=[django.core.validators.RegexValidator(message="Enter a valid international phone number using digits only.", regex="^\\d{10,15}$")])),
                ("group_name", models.CharField(blank=True, max_length=100)),
                ("source", models.CharField(choices=[("manual", "Manual"), ("customer", "System customer"), ("import", "CSV import"), ("other", "Other")], default="manual", max_length=20)),
                ("sms_allowed", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.CharField(blank=True, max_length=255)),
                ("customer", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sms_contact", to="core.customer")),
            ],
            options={"ordering": ["name", "normalized_phone"]},
        ),
        migrations.CreateModel(
            name="SmsImportBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("filename", models.CharField(max_length=200)),
                ("group_name", models.CharField(blank=True, max_length=100)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("imported_count", models.PositiveIntegerField(default=0)),
                ("updated_count", models.PositiveIntegerField(default=0)),
                ("skipped_count", models.PositiveIntegerField(default=0)),
                ("error_count", models.PositiveIntegerField(default=0)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sms_imports_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SmsTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("category", models.CharField(blank=True, max_length=80)),
                ("body", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sms_templates_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SmsCampaign",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(blank=True, max_length=160)),
                ("sender_id", models.CharField(max_length=20)),
                ("message", models.TextField()),
                ("recipient_count", models.PositiveIntegerField(default=0)),
                ("segment_count", models.PositiveIntegerField(default=1)),
                ("estimated_units", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("sending", "Sending"), ("sent", "Sent"), ("partial", "Partially sent"), ("failed", "Failed")], default="draft", max_length=20)),
                ("provider_response", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sms_campaigns_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SmsRecipient",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(blank=True, max_length=160)),
                ("phone", models.CharField(max_length=20, validators=[django.core.validators.RegexValidator(message="Enter a valid international phone number using digits only.", regex="^\\d{10,15}$")])),
                ("personalized_message", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("sent", "Sent"), ("delivered", "Delivered"), ("failed", "Failed")], default="queued", max_length=20)),
                ("provider_id", models.CharField(blank=True, max_length=120)),
                ("provider_status", models.CharField(blank=True, max_length=120)),
                ("error_message", models.CharField(blank=True, max_length=255)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("campaign", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recipients", to="sms_center.smscampaign")),
                ("contact", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="message_recipients", to="sms_center.smscontact")),
                ("customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sms_recipients", to="core.customer")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="smsrecipient",
            constraint=models.UniqueConstraint(fields=("campaign", "phone"), name="unique_sms_phone_per_campaign"),
        ),
    ]
