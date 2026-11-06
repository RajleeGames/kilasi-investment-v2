"""Booking boss SMS regression tests; do not contact the provider."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from kilasi_sms.automation import _event_still_eligible, enqueue_event
from kilasi_sms.models import KilasiSmsAutomaticJob, KilasiSmsAutomationRule
from kilasi_transport.models import Booking
from sms_center.models import SmsSenderProfile, SmsTemplate


@override_settings(TRANSPORT_BOSS_PHONE='0755123456', TRANSPORT_BOSS_NAME='Boss')
class BookingBossSmsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='sms_booking_boss', email='boss-tests@example.org', password='test-password')
        SmsSenderProfile.objects.create(sender_id='KILASI LTD', is_active=True)
        self.template = SmsTemplate.objects.create(
            name='KI Transport: New booking boss', category='Kilasi Transport',
            body='Booking {reference}: {customer} {customer_phone} {pickup} to {destination} {date} {amount}')
        KilasiSmsAutomationRule.objects.create(
            branch=None, department='transport', event_key='transport.booking_created_boss',
            template=self.template, sender_id='KILASI LTD',
            enabled=True, enabled_at=timezone.now())

    def make_booking(self, customer_phone='0712345678', status='pending'):
        with self.captureOnCommitCallbacks(execute=True):
            booking = Booking.objects.create(
                customer_name='Asha Ally', customer_phone=customer_phone,
                origin='Moshi', destination='Arusha', requested_date=timezone.localdate(),
                quoted_amount=120000, created_by=self.user, status=status)
        return booking

    def test_new_booking_queues_one_boss_message_using_existing_phone(self):
        with patch('kilasi_sms.automation._send_one_payload') as send:
            booking = self.make_booking()
            jobs = KilasiSmsAutomaticJob.objects.filter(event_key='transport.booking_created_boss')
            self.assertEqual(jobs.count(), 1)
            job = jobs.get()
            self.assertEqual(job.phone, '255755123456')
            self.assertEqual(job.branch, None)
            self.assertIn(booking.reference, job.body)
            self.assertIn('Asha Ally', job.body)
            self.assertIn('Moshi', job.body)
            self.assertIn('Arusha', job.body)
            self.assertIn('120000', job.body)
            self.assertEqual(job.status, 'queued')
            send.assert_not_called()
            self.assertEqual(enqueue_event('transport.booking_created_boss', Booking, booking.pk).pk, job.pk)
            booking.customer_name = 'Asha Updated'
            with self.captureOnCommitCallbacks(execute=True):
                booking.save()
            self.assertEqual(jobs.count(), 1)

    def test_boss_message_does_not_depend_on_customer_phone(self):
        self.make_booking(customer_phone='')
        self.assertEqual(KilasiSmsAutomaticJob.objects.filter(
            event_key='transport.booking_created_boss').count(), 1)

    def test_initially_confirmed_booking_notifies_boss(self):
        self.make_booking(status='confirmed')
        self.assertEqual(KilasiSmsAutomaticJob.objects.filter(
            event_key='transport.booking_created_boss').count(), 1)

    def test_cancelled_booking_cannot_be_sent(self):
        booking = self.make_booking()
        job = KilasiSmsAutomaticJob.objects.get(event_key='transport.booking_created_boss')
        booking.status = 'cancelled'
        with self.captureOnCommitCallbacks(execute=True):
            booking.save(update_fields=['status'])
        self.assertFalse(_event_still_eligible(job))

    def test_missing_boss_phone_queues_nothing(self):
        with override_settings(TRANSPORT_BOSS_PHONE=''):
            self.make_booking()
        self.assertFalse(KilasiSmsAutomaticJob.objects.exists())


class BookingBossRuleSeedTests(TestCase):
    def test_new_rule_inherits_trip_boss_rule_without_changing_trip_rule(self):
        owner = get_user_model().objects.create_superuser(
            username='hama_seed_owner', email='seed@example.org', password='test-password')
        template = SmsTemplate.objects.create(
            name='KI Transport: New trip boss', category='Kilasi Transport',
            body='Trip {reference}')
        SmsSenderProfile.objects.create(sender_id='KILASI LTD', is_active=True)
        trip_rule = KilasiSmsAutomationRule.objects.create(
            branch=None, department='transport', event_key='transport.trip_created_boss',
            template=template, sender_id='KILASI LTD', enabled=True,
            enabled_at=timezone.now(), changed_by=owner)
        call_command('seed_kilasi_sms')
        call_command('seed_kilasi_sms_automations')
        booking_rule = KilasiSmsAutomationRule.objects.get(
            branch__isnull=True, event_key='transport.booking_created_boss')
        self.assertEqual(booking_rule.sender_id, trip_rule.sender_id)
        self.assertTrue(booking_rule.enabled)
        self.assertIsNotNone(booking_rule.enabled_at)
        self.assertEqual(booking_rule.changed_by, owner)
        self.assertEqual(booking_rule.template.name, 'KI Transport: New booking boss')
        call_command('seed_kilasi_sms_automations')
        self.assertEqual(KilasiSmsAutomationRule.objects.filter(
            branch__isnull=True, event_key='transport.booking_created_boss').count(), 1)
        trip_rule.refresh_from_db()
        self.assertTrue(trip_rule.enabled)
