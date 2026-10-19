"""Role isolation, data boundaries, personalization and the no-send default."""
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from core.models import Branch, Customer, Sale
from kilasi_transport.models import Booking, TransportAccess
from sms_center.models import SmsSenderProfile, SmsCampaign, SmsContact
from .audiences import audience_rows, parse_filters
from .models import KilasiSmsDispatch
from .services import personalized

User=get_user_model()

class SmsHubTests(TestCase):
    def setUp(self):
        self.a=Branch.objects.create(name='Retail A')
        self.b=Branch.objects.create(name='Transport B')
        self.admin=User.objects.create_superuser('kilasi_owner','owner@example.com','pw_test')
        self.manager=User.objects.create_user('transport_mgr',password='pw_test')
        self.manager.profile.role='cashier'; self.manager.profile.branch=self.b
        self.manager.profile.save(update_fields=['role','branch'])
        TransportAccess.objects.create(user=self.manager, branch=self.b, role='manager')
        self.staff=User.objects.create_user('transport_staff',password='pw_test')
        self.staff.profile.role='cashier'; self.staff.profile.branch=self.b
        self.staff.profile.save(update_fields=['role','branch'])
        TransportAccess.objects.create(user=self.staff, branch=self.b, role='staff')
        SmsSenderProfile.objects.create(sender_id='KILASI',is_default=True,is_active=True)
        self.customer=Customer.objects.create(name='Asha Mushi', phone='0712345678')
        self.sale=Sale.objects.create(branch=self.a,customer=self.customer,
            cashier=self.admin, payment_method='debt',grand_total='50000.00')
        from django.utils import timezone
        self.booking=Booking.objects.create(branch=self.b,customer_name='Juma Ally',
            customer_phone='0755123456',origin='Moshi',destination='Arusha',
            requested_date=timezone.localdate(),created_by=self.admin)

    def test_personalization(self):
        row={'name':'Asha Mushi','phone':'255712345678','reference':'KI-TR-3',
             'pickup':'Moshi','destination':'Arusha','date':'2026-10-18','branch':'Transport B'}
        self.assertEqual(personalized('Habari {first_name}, safari {reference} kwenda {destination}',row),
            'Habari Asha, safari KI-TR-3 kwenda Arusha')
        self.assertIn('Ndugu Asha,',personalized('Asante kwa huduma',row))

    def test_retail_admin_and_transport_scope(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/retail/sms/').status_code,200)
        self.assertEqual(self.client.get('/transport/sms/').status_code,200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get('/transport/sms/').status_code,200)
        self.assertEqual(self.client.get('/retail/sms/').status_code,403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/transport/sms/').status_code,403)

    def test_branch_audience_isolation(self):
        from django.utils import timezone
        f=parse_filters({},'retail')
        self.assertEqual([r['key'] for r in audience_rows('retail',self.a,f)],[f'sales:{self.sale.pk}'])
        self.assertEqual(audience_rows('retail',self.b,f),[])
        f=parse_filters({},'transport')
        self.assertEqual([r['key'] for r in audience_rows('transport',self.b,f)],[f'bookings:{self.booking.pk}'])
        self.assertEqual([r['key'] for r in audience_rows('transport',self.a,f)],[f'bookings:{self.booking.pk}'])

    @override_settings(KILASI_SMS_LIVE_ENABLED=False,BEEM_API_KEY='dummy',BEEM_SECRET_KEY='dummy',BEEM_SENDER_ID='KILASI')
    def test_draft_requires_approval_and_never_sends_without_live_switch(self):
        self.client.force_login(self.manager)
        with patch('sms_center.services._send_one_payload') as provider:
            response=self.client.post('/transport/sms/draft/',{
                'title':'Booking test','sender_id':'KILASI',
                'message':'Ndugu {first_name}, booking {reference}',
                'source':'bookings','selected':[f'bookings:{self.booking.pk}'],
                'confirm_relationship':'on',
            })
            self.assertEqual(response.status_code,302)
            self.assertEqual(KilasiSmsDispatch.objects.count(),1)
            draft=KilasiSmsDispatch.objects.get()
            self.assertEqual(draft.status,'draft')
            response=self.client.post(f'/transport/sms/drafts/{draft.pk}/confirm/',{'confirm_send':'yes'})
            self.assertEqual(response.status_code,302)
            draft.refresh_from_db()
            self.assertEqual(draft.status,'draft')
            provider.assert_not_called()

    def test_branch_replay_token_is_rejected(self):
        self.client.force_login(self.manager)
        response=self.client.post('/transport/sms/draft/',{
            'title':'Wrong branch','sender_id':'KILASI','message':'Hi {name}',
            'source':'bookings', 'selected':['bookings:999999'],
            'confirm_relationship':'on',
        })
        self.assertEqual(response.status_code,302)
        self.assertEqual(KilasiSmsDispatch.objects.count(),0)

    def test_blocked_contact_skipped(self):
        SmsContact.objects.create(name='Juma',phone='0755123456',normalized_phone='255755123456',sms_allowed=False)
        self.client.force_login(self.manager)
        response=self.client.post('/transport/sms/draft/',{
            'title':'Opt-out','sender_id':'KILASI','message':'Hi {name}',
            'source':'bookings','selected':[f'bookings:{self.booking.pk}'],
            'confirm_relationship':'on',
        })
        self.assertEqual(response.status_code,302)
        self.assertFalse(SmsCampaign.objects.filter(title__contains='Opt-out').exists())
