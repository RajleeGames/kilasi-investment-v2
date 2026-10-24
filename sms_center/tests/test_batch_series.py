"""Regression suite for safe non-overlapping SMS batches."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from sms_center.batch_service import make_series, prepare_next, process_batch_outbox
from sms_center.models import SmsBatchEntry, SmsContact, SmsSenderProfile


@override_settings(KILASI_SMS_LIVE_ENABLED=True, KILASI_SMS_AUTOMATION_ENABLED=True,
                   BEEM_API_KEY='testkey', BEEM_SECRET_KEY='testsecret', BEEM_SENDER_ID='KILASI LTD',
                   SMS_MAX_RECIPIENTS_PER_SEND=3)
class SmsBatchSeriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('batch_admin', 'test@example.test', 'pass-for-testing')
        SmsSenderProfile.objects.create(sender_id='KILASI LTD', is_active=True, is_default=True)
        for i in range(8):
            SmsContact.objects.create(name=f'Customer {i}', phone=f'2557123456{i:02d}',
                                      normalized_phone=f'2557123456{i:02d}',group_name='HAMA TEST',
                                      sms_allowed=True, is_active=True)
        SmsContact.objects.create(name='Opted Out',phone='255713333333',
                                  normalized_phone='255713333333',group_name='HAMA TEST',
                                  sms_allowed=False,is_active=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_series_snapshots_eligible_only(self):
        series = make_series(actor=self.admin, title='Test Campaign', group='HAMA TEST')
        self.assertEqual(series.entries.count(), 8)
        self.assertEqual(series.entries.filter(status='pending').count(), 8)

    def test_first_and_next_batch_never_overlap(self):
        series = make_series(actor=self.admin, title='Test Campaign', group='HAMA TEST')
        first, _, n = prepare_next(series_id=series.pk, sender_id='KILASI LTD',
                                   body='Hello {first_name}', actor=self.admin)
        self.assertEqual(n,3)
        with self.assertRaisesMessage(Exception, 'Previous batch'):
            prepare_next(series_id=series.pk, sender_id='KILASI LTD', body='Hello', actor=self.admin)
        with patch('sms_center.batch_service._send_one_payload', return_value=(True, 200, {'successful': True})):
            process_batch_outbox(limit=20)
        second, _, n2 = prepare_next(series_id=series.pk, sender_id='KILASI LTD',
                                      body='Thank you {name}', actor=self.admin)
        self.assertEqual(n2,3)
        first_phones=set(first.recipients.values_list('phone',flat=True))
        second_phones=set(second.recipients.values_list('phone',flat=True))
        self.assertFalse(first_phones & second_phones)
        self.assertEqual(series.entries.filter(status='pending').count(),2)

    def test_opt_out_after_snapshot_is_excluded(self):
        series=make_series(actor=self.admin, title='Test campaign', group='HAMA TEST')
        one=SmsContact.objects.filter(group_name='HAMA TEST',sms_allowed=True).order_by('pk').first()
        one.sms_allowed=False; one.save(update_fields=['sms_allowed'])
        campaign, _, count=prepare_next(series_id=series.pk, sender_id='KILASI LTD',
                                         body='Hello', actor=self.admin)
        self.assertEqual(count,3)
        self.assertNotIn(one.normalized_phone,campaign.recipients.values_list('phone',flat=True))
        self.assertEqual(SmsBatchEntry.objects.get(series=series,phone=one.normalized_phone).status,'blocked')

    def test_unknown_provider_does_not_auto_retry(self):
        series=make_series(actor=self.admin, title='Test', group='HAMA TEST')
        campaign, _, _=prepare_next(series_id=series.pk, sender_id='KILASI LTD',body='Hello',actor=self.admin)
        with patch('sms_center.batch_service._send_one_payload', return_value=(False,0,{'error':'timeout'})) as send:
            process_batch_outbox(limit=20)
            process_batch_outbox(limit=20)
            self.assertEqual(send.call_count,3)
        self.assertEqual(series.entries.filter(status='review').count(),3)
        self.assertEqual(series.entries.filter(status='queued').count(),0)

    def test_admin_only_screen_and_no_send_on_get(self):
        series=make_series(actor=self.admin, title='Test', group='HAMA TEST')
        response=self.client.get(reverse('sms_center:batch_detail',args=[series.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Next')
        self.assertEqual(series.entries.filter(status='pending').count(),8)
        self.client.logout()
        self.assertNotEqual(self.client.get(reverse('sms_center:batches')).status_code,200)
