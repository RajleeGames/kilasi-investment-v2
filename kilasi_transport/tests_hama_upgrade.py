"""2026 HAMA upgrades. Run python manage.py test kilasi_transport.tests_hama_upgrade"""
from datetime import timedelta, time
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Booking, Driver, TransportAccess, Trip, TripPayment, TransportExpense, Vehicle
from .forms import BookingForm, TripForm, TripPaymentForm, expense_trips_for_day
from kilasi_sms.services import personalized
from kilasi_sms.automation import _event_row


@override_settings(KILASI_SMS_LIVE_ENABLED=False, KILASI_SMS_AUTOMATION_ENABLED=False,
                   TRANSPORT_BOSS_PHONE='255780850748', TRANSPORT_BOSS_NAME='Boss')
class HamaUpgradeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.admin=User.objects.create_superuser('hama_upgrade_admin','hama@test.invalid','strong-password')
        cls.driver_user=User.objects.create_user('hama_upgrade_driver',password='strong-password')
        cls.driver=Driver.objects.create(full_name='Salim Driver')
        cls.vehicle=Vehicle.objects.create(registration='T-HAMA-001',make_model='Toyota')
        TransportAccess.objects.create(user=cls.driver_user,role='driver',driver=cls.driver)
        cls.when=timezone.now()+timedelta(days=2)
        cls.trip=Trip.objects.create(driver=cls.driver,vehicle=cls.vehicle,created_by=cls.admin,
            customer_name='Juma',customer_phone='0712345678',origin='Moshi',destination='Arusha',
            scheduled_start=cls.when,agreed_fee=Decimal('300000'))

    def setUp(self):self.client.force_login(self.admin)

    def test_single_start_trip_form(self):
        self.assertIsNone(self.trip.scheduled_end)
        self.assertNotIn('scheduled_end',TripForm().fields)

    def test_boss_customer_phone_token(self):
        row=_event_row('transport.trip_created_boss',self.trip)
        self.assertEqual(row['phone'],'255780850748')
        self.assertEqual(row['customer_phone'],'0712345678')
        self.assertIn('0712345678',personalized('Mteja {customer}: {customer_phone}',row))

    def test_booking_time_and_overdue(self):
        booking=Booking.objects.create(customer_name='Juma',origin='Moshi',destination='Arusha',
            requested_date=timezone.localdate()-timedelta(days=1),requested_time=time(9,30),created_by=self.admin)
        self.assertTrue(booking.is_overdue)
        booking.status='completed'
        self.assertFalse(booking.is_overdue)
        self.assertIn('requested_time',BookingForm().fields)

    def test_payment_filter_and_history(self):
        self.trip.status='completed';self.trip.completed_at=timezone.now();self.trip.save(update_fields=['status','completed_at'])
        TripPayment.objects.create(trip=self.trip,amount=Decimal('120000'),created_by=self.admin)
        res=self.client.get(reverse('transport:trips'),{'payment':'partial'})
        self.assertContains(res,self.trip.reference)
        self.assertEqual(self.trip.payment_state,'Partially paid')
        res=self.client.get(reverse('transport:trips'),{'payment':'paid'})
        self.assertNotContains(res,self.trip.reference)
        TripPayment.objects.create(trip=self.trip,amount=Decimal('180000'),created_by=self.admin)
        self.assertEqual(self.trip.payment_state,'Fully paid')
        self.assertIsNotNone(self.trip.fully_paid_at)
        res=self.client.get(reverse('transport:trips'),{'payment':'paid'})
        self.assertContains(res,self.trip.reference)

    def test_expense_day_trip_label_and_endpoint(self):
        day=timezone.localtime(self.when).date()
        self.assertTrue(expense_trips_for_day(day).filter(pk=self.trip.pk).exists())
        res=self.client.get(reverse('transport:expense_trip_options'),{'date':day.isoformat()})
        self.assertEqual(res.status_code,200)
        self.assertIn('Salim Driver',str(res.json()))
        self.assertEqual(self.client.get(reverse('transport:expense_trip_options'),{'date':'invalid'}).status_code,400)

    def test_driver_resume_and_retail_still_forbidden(self):
        self.client.force_login(self.driver_user)
        self.assertRedirects(self.client.get('/'),reverse('transport:driver_home'))
        self.assertEqual(self.client.get('/transport/').status_code,302)
        self.assertEqual(self.client.get('/sms/').status_code,403)

    def test_cannot_start_overlapping_in_progress(self):
        other_driver=Driver.objects.create(full_name='Another Driver')
        other=Trip.objects.create(driver=other_driver,vehicle=self.vehicle,created_by=self.admin,
            customer_name='Other',origin='Arusha',destination='Moshi',
            scheduled_start=self.when+timedelta(hours=1),agreed_fee=Decimal('100000'))
        self.client.post(reverse('transport:trip_status',args=[self.trip.pk]),{'status':'in_progress'})
        self.client.post(reverse('transport:trip_status',args=[other.pk]),{'status':'in_progress'})
        other.refresh_from_db()
        self.assertEqual(other.status,'planned')
