"""Add durable, non-overlapping SMS batch series; no existing SMS records changed."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('sms_center', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='SmsBatchSeries',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=160)),
                ('group_name', models.CharField(blank=True, max_length=100)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name='sms_batch_series_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
        migrations.CreateModel(
            name='SmsBatchEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('phone', models.CharField(max_length=20)),
                ('name', models.CharField(blank=True, max_length=160)),
                ('status', models.CharField(max_length=12, db_index=True, default='pending', choices=[
                    ('pending','Not selected'),('queued','Queued'),('sending','Provider review if interrupted'),
                    ('accepted','Accepted by Beem'),('review','Not accepted or uncertain'),('blocked','Opted out or invalid'),
                ])),
                ('batch_number', models.PositiveIntegerField(blank=True, null=True)),
                ('contact', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='sms_batch_entries', to='sms_center.smscontact')),
                ('campaign', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='batch_entries', to='sms_center.smscampaign')),
                ('recipient', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='batch_entry', to='sms_center.smsrecipient')),
                ('series', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='entries', to='sms_center.smsbatchseries')),
            ],
            options={'ordering': ['pk']},
        ),
        migrations.AddConstraint(
            model_name='smsbatchentry',
            constraint=models.UniqueConstraint(fields=['series', 'phone'], name='sms_batch_unique_phone'),
        ),
    ]
