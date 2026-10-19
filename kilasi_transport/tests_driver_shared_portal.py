"""Run python manage.py test kilasi_transport.tests_driver_shared_portal"""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Booking, Driver, TransportAccess, TransportExpense, Trip, TripPayment, Vehicle


@override_settings(KILASI_SMS_LIVE_ENABLED=False, KILASI_SMS_AUTOMATION_ENABLED=False)
class DriverPortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user('ki_driver_test', password='Test!Secure1234')
        cls.other_user = User.objects.create_user('ki_other_driver', password='Test!Secure1234')
        cls.manager = User.objects.create_superuser('ki_driver_manager', 'manager@example.test', 'Test!Secure1234')
        cls.driver = Driver.objects.create(full_name='Driver One', status='active')
        cls.other_driver = Driver.objects.create(full_name='Driver Two', status='active')
        TransportAccess.objects.create(user=cls.user, role='driver', driver=cls.driver, is_active=True)
        TransportAccess.objects.create(user=cls.other_user, role='driver', driver=cls.other_driver, is_active=True)
        cls.vehicle = Vehicle.objects.create(registration='T 913 DRV', make_model='Toyota', status='active')
        cls.other_vehicle = Vehicle.objects.create(registration='T 914 DRV', make_model='Isuzu', status='active')
        start = timezone.now() + timedelta(days=2)
        cls.trip = Trip.objects.create(driver=cls.driver, vehicle=cls.vehicle, created_by=cls.user,
            customer_name='My Customer', customer_phone='0712345678', origin='Moshi', destination='Arusha',
            scheduled_start=start, scheduled_end=start+timedelta(hours=4), agreed_fee=Decimal('90000'))
        cls.other_trip = Trip.objects.create(driver=cls.other_driver, vehicle=cls.other_vehicle, created_by=cls.other_user,
            customer_name='Other Customer', origin='Arusha', destination='Moshi',
            scheduled_start=start, scheduled_end=start+timedelta(hours=4), agreed_fee=Decimal('130000'))

    def setUp(self):
        self.client.force_login(self.user)

    def test_driver_only_sees_own_trips_and_shared_base(self):
        response = self.client.get(reverse('transport:driver_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Customer')
        self.assertNotContains(response, 'Other Customer')
        self.assertTemplateUsed(response, 'base.html')
        self.assertNotContains(response, 'driver.css')

    def test_driver_cannot_open_another_drivers_trip(self):
        response = self.client.get(reverse('transport:driver_trip_detail', args=[self.other_trip.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse('transport:driver_trip_status', args=[self.other_trip.pk]), {'status':'in_progress'})
        self.assertEqual(response.status_code, 404)
        self.other_trip.refresh_from_db()
        self.assertEqual(self.other_trip.status, 'planned')

    def test_driver_cannot_use_retail_sms_or_manager_pages(self):
        for url in ('/pos/', '/transport/reports/', '/transport/expenses/', '/transport/users/',
                    '/transport/trips/', '/transport/sms/automations/', '/sms/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_driver_start_complete_order(self):
        url = reverse('transport:driver_trip_status', args=[self.trip.pk])
        response = self.client.post(url, {'status':'completed'})
        self.assertRedirects(response, reverse('transport:driver_trip_detail', args=[self.trip.pk]))
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')
        self.client.post(url, {'status':'in_progress'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'in_progress')
        self.client.post(url, {'status':'completed'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'completed')
        self.assertIsNotNone(self.trip.completed_at)
        self.client.post(url, {'status':'in_progress'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'completed')

    def test_post_cannot_reassign_own_driver(self):
        start = timezone.now() + timedelta(days=10)
        data = {'vehicle':self.other_vehicle.pk,'driver':self.other_driver.pk,
                'customer_name':'Fresh Customer','customer_phone':'0712345678',
                'origin':'Moshi','destination':'Dar es Salaam',
                'scheduled_start':timezone.localtime(start).strftime('%Y-%m-%dT%H:%M'),
                'scheduled_end':timezone.localtime(start+timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M'),
                'agreed_fee':'100000','status':'completed'}
        response = self.client.post(reverse('transport:driver_trip_new'), data)
        self.assertEqual(response.status_code, 302)
        trip = Trip.objects.get(customer_name='Fresh Customer')
        self.assertEqual(trip.driver_id, self.driver.pk)
        self.assertEqual(trip.status, 'planned')
        self.assertEqual(trip.branch_id, None)

    def test_driver_filter_and_pagination(self):
        response = self.client.get(reverse('transport:driver_home'), {'q':'My Customer','status':'planned'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        response = self.client.get(reverse('transport:driver_home'), {'q':'Unmatched'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_suspended_driver_denied(self):
        access = TransportAccess.objects.get(user=self.user)
        access.is_active = False
        access.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('transport:driver_home')).status_code, 403)

    def test_non_driver_denied(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('transport:driver_home')).status_code, 403)

    def test_login_redirects_driver_workspace(self):
        self.client.logout()
        response = self.client.post(reverse('login'), {'username': 'ki_driver_test', 'password':'Test!Secure1234'})
        self.assertRedirects(response, reverse('transport:driver_home'))

    def test_driver_welcome_and_no_booking_controls(self):
        response = self.client.get(reverse('transport:driver_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Welcome back, Driver One!')
        self.assertContains(response, 'My assigned trips')
        self.assertNotContains(response, 'New booking request')
        self.assertNotContains(response, 'Other Customer')
        self.assertEqual(self.client.get('/transport/bookings/').status_code, 403)

    def test_driver_void_with_reason_audit(self):
        url = reverse('transport:driver_trip_status', args=[self.trip.pk])
        self.client.post(url, {'status':'cancelled', 'reason':'Wrong customer entered'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'cancelled')
        self.assertEqual(self.trip.cancel_reason, 'Wrong customer entered')
        self.assertEqual(self.trip.cancelled_by_id, self.user.pk)
        self.assertIsNotNone(self.trip.cancelled_at)
        self.client.post(url, {'status':'in_progress'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'cancelled')

    def test_driver_void_requires_reason(self):
        url = reverse('transport:driver_trip_status', args=[self.trip.pk])
        self.client.post(url, {'status':'cancelled', 'reason':'bad'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')
        self.assertIsNone(self.trip.cancelled_at)

    def test_driver_cannot_void_another_drivers_trip(self):
        url = reverse('transport:driver_trip_status', args=[self.other_trip.pk])
        response = self.client.post(url, {'status':'cancelled', 'reason':'Wrong entry'})
        self.assertEqual(response.status_code, 404)
        self.other_trip.refresh_from_db()
        self.assertEqual(self.other_trip.status, 'planned')

    def test_driver_cannot_void_paid_trip(self):
        TripPayment.objects.create(trip=self.trip, amount=Decimal('10000'), created_by=self.manager)
        self.client.post(reverse('transport:driver_trip_status', args=[self.trip.pk]),
                         {'status':'cancelled', 'reason':'Incorrect trip'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')

    def test_driver_cannot_void_trip_with_active_booking(self):
        Booking.objects.create(trip=self.trip, created_by=self.manager, customer_name='Customer A',
                               origin='Moshi', destination='Arusha', requested_date=timezone.localdate())
        self.client.post(reverse('transport:driver_trip_status', args=[self.trip.pk]),
                         {'status':'cancelled', 'reason':'Incorrect trip'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')

    def test_driver_cannot_void_trip_with_posted_expense(self):
        TransportExpense.objects.create(trip=self.trip, created_by=self.manager,
                                        description='Fuel', amount=Decimal('20000'))
        self.client.post(reverse('transport:driver_trip_status', args=[self.trip.pk]),
                         {'status':'cancelled', 'reason':'Incorrect trip'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')

    def test_in_progress_trip_can_be_voided_without_financial_links(self):
        self.trip.status = 'in_progress'
        self.trip.save(update_fields=['status'])
        self.client.post(reverse('transport:driver_trip_status', args=[self.trip.pk]),
                         {'status':'cancelled', 'reason':'Wrong destination supplied'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'cancelled')
        self.assertEqual(self.trip.cancelled_by_id, self.user.pk)

    def test_completed_trip_cannot_be_voided_by_driver(self):
        self.trip.status = 'completed'
        self.trip.completed_at = timezone.now()
        self.trip.save(update_fields=['status', 'completed_at'])
        self.client.post(reverse('transport:driver_trip_status', args=[self.trip.pk]),
                         {'status':'cancelled', 'reason':'Incorrect trip'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'completed')
