from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('kilasi_transport', '0004_trip_cancellation_audit'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transportaccess', name='role',
            field=models.CharField(max_length=16, default='staff', choices=[
                ('manager', 'Transport Manager'),
                ('staff', 'Transport Staff'),
                ('driver', 'Transport Driver'),
            ]),
        ),
        migrations.AddField(
            model_name='transportaccess', name='driver',
            field=models.OneToOneField(
                to='kilasi_transport.driver', related_name='portal_access',
                null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
            ),
        ),
    ]
