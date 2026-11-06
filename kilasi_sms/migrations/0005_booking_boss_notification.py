# Booking-boss SMS: new rule choice only; existing jobs and rules untouched.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('kilasi_sms', '0004_transport_delivery_retries')]

    operations = [
        migrations.AlterField(
            model_name='kilasismsautomationrule',
            name='event_key',
            field=models.CharField(max_length=80, choices=[
                ('retail.sale_thanks', 'After retail sale'),
                ('retail.delivery_prepared', 'Delivery note prepared'),
                ('retail.delivery_completed', 'Delivery confirmed received'),
                ('transport.booking_received', 'Booking received'),
                ('transport.booking_confirmed', 'Booking confirmed'),
                ('transport.booking_created_boss', 'New booking boss notification'),
                ('transport.trip_created', 'New trip customer'),
                ('transport.trip_created_boss', 'New trip boss notification'),
                ('transport.trip_reminder', '24-hour trip reminder'),
                ('transport.trip_underway', 'Trip started'),
                ('transport.trip_completed', 'Trip completed'),
            ]),
        ),
    ]
