# Generated for KILASI V2. No historical data modifications.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('kilasi_transport', '0003_hama_standalone'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='trip',
            name='cancel_reason',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='trip',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trip',
            name='cancelled_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='kilasi_trips_cancelled', to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
