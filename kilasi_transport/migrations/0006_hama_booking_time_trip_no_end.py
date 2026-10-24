"""HAMA operation upgrades: booking time and unknown trip arrival.

Safe / non-destructive: old booking times are NULL and historic end values stay.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('kilasi_transport', '0005_driver_shared_portal')]

    operations = [
        migrations.AddField(
            model_name='booking', name='requested_time',
            field=models.TimeField(null=True, blank=True,
                help_text='Scheduled service time; may be unknown on older records.'),
        ),
        migrations.AlterField(
            model_name='trip', name='scheduled_end',
            field=models.DateTimeField(null=True, blank=True,
                help_text='Legacy optional arrival time; new trips have no estimated end time.'),
        ),
    ]
