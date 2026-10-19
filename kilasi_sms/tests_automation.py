"""Focused tests for scoped rule management, automatic SMS and idempotency."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from core.models import Branch, Customer, Sale
from kilasi_transport.models import Booking, TransportAccess
from sms_center.models import SmsSenderProfile, SmsTemplate, SmsCampaign
from kilasi_sms.models import KilasiSmsAutomaticJob, KilasiSmsAutomationRule
from kilasi_sms.automation import enqueue_event, deliver_job

User=get_user_model()


class AutomationTests(TestCase):
    def setUp(self):
        self.a=Branch.objects.create(name='A')
        self.b=Branch.objects.create(name='B')
        self.owner=User.objects.create_superuser('sms_owner','sms@example.com','testpw')
        self.manager=User.objects.create_user('hama_manager',password='testpw')
        self.manager.profile.role='cashier'
        self.manager.profile.branch=self.a
        self.manager.profile.save()
        TransportAccess.objects.create(user=self.manager,branch=self.a,role='manager')
        self.sender=SmsSenderProfile.objects.create(sender_id='KILASI',is_active=True)
        self.template=SmsTemplate.objects.create(name='Test booking',category='Kilasi Transport',
                                                body='Booking {reference} for {first_name} from {pickup} to {destination}')
        self.rule=KilasiSmsAutomationRule.objects.create(branch=None,department='transport',
            event_key='transport.booking_received',template=self.template,
            sender_id='KILASI',enabled=True,enabled_at=timezone.now())

    def booking(self,branch):
        return Booking.objects.create(branch=branch,customer_name='Asha Ally',customer_phone='0712345678',
            origin='Moshi',destination='Arusha',requested_date=timezone.localdate(),created_by=self.owner)

    def test_idempotency_and_branch(self):
        x=self.booking(self.a)
        a=enqueue_event('transport.booking_received',Booking,x.pk)
        b=enqueue_event('transport.booking_received',Booking,x.pk)
        self.assertEqual(a.pk,b.pk)
        self.assertEqual(KilasiSmsAutomaticJob.objects.count(),1)
        y=self.booking(self.b)
        enqueue_event('transport.booking_received',Booking,y.pk)
        self.assertEqual(KilasiSmsAutomaticJob.objects.count(),2)  # Both Transport customers, regardless of Retail branch.

    @override_settings(KILASI_SMS_LIVE_ENABLED=False,KILASI_SMS_AUTOMATION_ENABLED=False)
    def test_off_switch_never_sends(self):
        x=self.booking(self.a)
        job=enqueue_event('transport.booking_received',Booking,x.pk)
        with patch('kilasi_sms.automation._send_one_payload') as fn:
            self.assertFalse(deliver_job(job.pk))
            fn.assert_not_called()

    def test_department_firewall(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get('/transport/sms/automations/').status_code,200)
        self.assertEqual(self.client.get('/retail/sms/automations/').status_code,403)

    def test_no_duplicate_after_failed_provider(self):
        x=self.booking(self.a)
        job=enqueue_event('transport.booking_received',Booking,x.pk)
        with override_settings(KILASI_SMS_LIVE_ENABLED=True,KILASI_SMS_AUTOMATION_ENABLED=True,
                               BEEM_API_KEY='fake',BEEM_SECRET_KEY='fake',BEEM_SENDER_ID='KILASI'):
            with patch('kilasi_sms.automation._send_one_payload',return_value=(False,504,{'error':'timeout'})) as fn:
                self.assertFalse(deliver_job(job.pk))
                self.assertFalse(deliver_job(job.pk))
                self.assertEqual(fn.call_count,1)
        job.refresh_from_db()
        self.assertEqual(job.status,'unknown')
