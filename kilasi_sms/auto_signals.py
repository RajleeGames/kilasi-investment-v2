"""Queue lifecycle messages *after transaction commit*. Never contacts Beem."""
import logging
from django.db import transaction
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from core.models import Sale
from kilasi_transport.models import Booking, Trip
from kilasi_billing.models import DeliveryNote
from .automation import enqueue_event


logger=logging.getLogger(__name__)

def later(key, model, pk):
    def enqueue_safely(event=key, cls=model, ident=pk):
        try:
            enqueue_event(event, cls, ident)
        except Exception:
            # The business transaction is already committed; queue problems must
            # be logged, not reported as a sale/booking failure to the user.
            logger.exception('SMS automatic enqueue failed for %s #%s', event, ident)
    transaction.on_commit(enqueue_safely)


@receiver(pre_save, sender=Booking, dispatch_uid='kilasi5b_booking_prev')
def prior_booking(sender, instance, **kwargs):
    instance._sms_previous_status=(Booking.objects.filter(pk=instance.pk)
                                   .values_list('status',flat=True).first()) if instance.pk else None


@receiver(post_save, sender=Booking, dispatch_uid='kilasi5b_booking_after')
def after_booking(sender, instance, created, **kwargs):
    if created and instance.status=='pending':later('transport.booking_received',Booking,instance.pk)
    if instance.status=='confirmed' and (created or instance._sms_previous_status!='confirmed'):
        later('transport.booking_confirmed',Booking,instance.pk)


@receiver(pre_save, sender=Trip, dispatch_uid='kilasi5b_trip_prev')
def prior_trip(sender, instance, **kwargs):
    instance._sms_previous_status=(Trip.objects.filter(pk=instance.pk)
                                   .values_list('status',flat=True).first()) if instance.pk else None


@receiver(post_save, sender=Trip, dispatch_uid='kilasi5b_trip_after')
def after_trip(sender, instance, created, **kwargs):
    if created:
        later('transport.trip_created',Trip,instance.pk)
        later('transport.trip_created_boss',Trip,instance.pk)
    if instance.status=='in_progress' and (created or instance._sms_previous_status!='in_progress'):
        later('transport.trip_underway',Trip,instance.pk)
    if instance.status=='completed' and (created or instance._sms_previous_status!='completed'):
        later('transport.trip_completed',Trip,instance.pk)


@receiver(post_save, sender=Sale, dispatch_uid='kilasi5b_sale_after')
def after_sale(sender, instance, created, **kwargs):
    if created:later('retail.sale_thanks',Sale,instance.pk)


@receiver(post_save, sender=DeliveryNote, dispatch_uid='kilasi5b_delivery_after')
def after_delivery_note(sender, instance, created, **kwargs):
    if created:later('retail.delivery_prepared',DeliveryNote,instance.pk)
