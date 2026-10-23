# Safe, additive schema for Kilasi V2 HAMA SMS retries, 2026-10-23.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('kilasi_sms', '0003_hama_standalone'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='kilasismsautomaticjob', name='attempt_count',
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AlterField(model_name='kilasismsautomaticjob', name='status',
            field=models.CharField(max_length=16,db_index=True,default='queued',choices=[
                ('queued','Queued'),('sending','Sending — review before retry'),
                ('accepted','Accepted by Beem'),('blocked','Opted out / ineligible'),
                ('unknown','Failed or uncertain — no auto retry'),('cancelled','Cancelled'),
                ('rejected','Definitely rejected — review or retry'),
            ])),
        migrations.AlterField(model_name='kilasismsautomationrule', name='event_key',
            field=models.CharField(max_length=80,choices=[
                ('retail.sale_thanks','After retail sale'),('retail.delivery_prepared','Delivery note prepared'),
                ('retail.delivery_completed','Delivery confirmed received'),
                ('transport.booking_received','Booking received'),
                ('transport.booking_confirmed','Booking confirmed'),
                ('transport.trip_created','New trip customer'),
                ('transport.trip_created_boss','New trip boss notification'),
                ('transport.trip_reminder','24-hour trip reminder'),
                ('transport.trip_underway','Trip started'),
                ('transport.trip_completed','Trip completed'),
            ])),
        migrations.CreateModel(name='KilasiSmsRetryAudit',fields=[
            ('id',models.BigAutoField(primary_key=True,serialize=False,auto_created=True,verbose_name='ID')),
            ('previous_status',models.CharField(max_length=16)),
            ('reason',models.CharField(max_length=240)),
            ('previous_campaign_id',models.PositiveBigIntegerField(null=True,blank=True)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('job',models.ForeignKey(to='kilasi_sms.kilasismsautomaticjob',related_name='retry_audits',on_delete=django.db.models.deletion.PROTECT)),
            ('requested_by',models.ForeignKey(to=settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=django.db.models.deletion.PROTECT)),
        ],options={'ordering':['-created_at']}),
    ]
