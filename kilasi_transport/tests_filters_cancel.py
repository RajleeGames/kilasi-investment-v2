"""Regression tests for transport filtering, cancellation, and financial safety."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Booking, Driver, TransportExpense, Trip, TripPayment, Vehicle


class TransportFilterAndCancelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('ki_filter_admin', 'test@example.test', 'testing-password')
        cls.vehicle = Vehicle.objects.create(registration='TEST-T-100', make_model='Test truck')
        cls.driver = Driver.objects.create(full_name='Test Driver')
        start = timezone.now() + timedelta(days=5)
        cls.trip = Trip.objects.create(
            vehicle=cls.vehicle, driver=cls.driver, customer_name='Alice Test',
            origin='Moshi', destination='Arusha', scheduled_start=start,
            scheduled_end=start + timedelta(hours=3), agreed_fee=Decimal('200000'),
            created_by=cls.admin,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_cancel_needs_reason_and_is_post_only(self):
        url = reverse('transport:trip_status', args=[self.trip.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'status': 'cancelled', 'reason': ''})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')
        self.client.post(url, {'status': 'cancelled', 'reason': 'Customer withdrew request'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'cancelled')
        self.assertEqual(self.trip.cancel_reason, 'Customer withdrew request')
        self.assertEqual(self.trip.cancelled_by, self.admin)
        self.assertIsNotNone(self.trip.cancelled_at)
        self.client.post(url, {'status': 'cancelled', 'reason': 'Again'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.cancel_reason, 'Customer withdrew request')

    def test_active_payment_blocks_cancel(self):
        TripPayment.objects.create(trip=self.trip, amount=Decimal('1000'), created_by=self.admin)
        self.client.post(reverse('transport:trip_status', args=[self.trip.pk]),
                         {'status': 'cancelled', 'reason': 'Mistake in trip entry'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')

    def test_linked_open_booking_blocks_cancel(self):
        Booking.objects.create(trip=self.trip, customer_name='Alice', origin='Moshi',
                               destination='Arusha', requested_date=timezone.localdate(),
                               created_by=self.admin)
        self.client.post(reverse('transport:trip_status', args=[self.trip.pk]),
                         {'status': 'cancelled', 'reason': 'Wrong trip entry'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'planned')

    def test_trip_dates_and_status_filter(self):
        day = timezone.localtime(self.trip.scheduled_start).date().isoformat()
        res = self.client.get(reverse('transport:trips'), {'start': day, 'end': day, 'status': 'planned'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.trip.reference)
        res = self.client.get(reverse('transport:trips'), {'status': 'completed'})
        self.assertNotContains(res, self.trip.reference)

    def test_booking_expenses_and_vehicle_filters(self):
        day = timezone.localdate()
        Booking.objects.create(customer_name='Alice booking', origin='Moshi', destination='Arusha',
                               requested_date=day, created_by=self.admin)
        TransportExpense.objects.create(description='Fuel sample', amount=Decimal('3000'),
                                        expense_date=day, created_by=self.admin)
        for path in ('transport:bookings', 'transport:expenses'):
            res = self.client.get(reverse(path), {'start': day.isoformat(), 'end': day.isoformat()})
            self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get(reverse('transport:vehicles')).status_code, 200)
        self.assertEqual(self.client.get(reverse('transport:drivers')).status_code, 200)
