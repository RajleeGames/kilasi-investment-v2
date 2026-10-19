"""Run: python manage.py test kilasi_transport"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Branch
from .forms import TripForm
from .models import Booking, Driver, TransportExpense, Trip, TripPayment, Vehicle


class TransportCoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username='transport_admin', email='transport@example.test', password='TestPass@12345')
        cls.b1 = Branch.objects.create(name='Transport Branch A')
        cls.b2 = Branch.objects.create(name='Transport Branch B')
        cls.v = Vehicle.objects.create(registration='T 123 AAA', make_model='Toyota Dyna')
        cls.d = Driver.objects.create(full_name='Driver Test')
        now = timezone.now() + timedelta(days=1)
        cls.trip = Trip.objects.create(branch=cls.b1, vehicle=cls.v, driver=cls.d,
                                      customer_name='Client A', origin='Moshi', destination='Arusha',
                                      scheduled_start=now, scheduled_end=now + timedelta(hours=3),
                                      agreed_fee=Decimal('250000'), created_by=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['novman_branch_id'] = self.b1.pk
        session.save()

    def test_trip_reference_and_company_wide_visibility(self):
        self.assertTrue(self.trip.reference.startswith('KI-TR-'))
        response = self.client.get(reverse('transport:trip_detail', args=[self.trip.pk]))
        self.assertEqual(response.status_code, 200)
        session = self.client.session
        session['novman_branch_id'] = self.b2.pk
        session.save()
        response = self.client.get(reverse('transport:trip_detail', args=[self.trip.pk]))
        self.assertEqual(response.status_code, 200)  # Same Transport records after Retail switch.

    def test_conflicting_vehicle_or_driver_denied(self):
        start = timezone.localtime(self.trip.scheduled_start).strftime('%Y-%m-%dT%H:%M')
        end = timezone.localtime(self.trip.scheduled_end).strftime('%Y-%m-%dT%H:%M')
        form = TripForm(data={'vehicle':self.v.pk, 'driver':self.d.pk, 'customer_name':'Customer B',
                              'origin':'Arusha', 'destination':'Moshi', 'scheduled_start':start,
                              'scheduled_end':end, 'agreed_fee':'250000'}, branch=self.b1)
        self.assertFalse(form.is_valid())
        self.assertIn('another trip', str(form.non_field_errors()))

    def test_cannot_post_payment_to_planned_trip(self):
        response = self.client.post(reverse('transport:payment_new', args=[self.trip.pk]),
                                    {'amount':'10000','method':'cash'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TripPayment.objects.count(), 0)

    def test_start_then_pay_and_reject_overpayment(self):
        self.client.post(reverse('transport:trip_status', args=[self.trip.pk]), {'status':'in_progress'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status,'in_progress')
        self.client.post(reverse('transport:payment_new', args=[self.trip.pk]), {'amount':'20000','method':'cash'})
        self.assertEqual(TripPayment.objects.filter(trip=self.trip, is_voided=False).count(), 1)
        self.client.post(reverse('transport:payment_new', args=[self.trip.pk]), {'amount':'400000','method':'cash'})
        self.assertEqual(TripPayment.objects.filter(trip=self.trip).count(), 1)
        self.assertEqual(self.trip.balance, Decimal('230000'))
        # No cancellation while an active payment is recorded.
        self.client.post(reverse('transport:trip_status', args=[self.trip.pk]), {'status':'cancelled'})
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.status, 'in_progress')

    def test_expenses_and_bookings_do_not_touch_retail_tables(self):
        from core.models import Sale
        old_count = Sale.objects.count()
        Booking.objects.create(branch=self.b1, customer_name='A', origin='Moshi',destination='Arusha',
                               requested_date=timezone.localdate(), created_by=self.admin)
        TransportExpense.objects.create(branch=self.b1, description='Fuel', amount=Decimal('50000'),
                                        created_by=self.admin)
        self.assertEqual(Sale.objects.count(),old_count)

    def test_cashier_cannot_access_transport(self):
        u = get_user_model().objects.create_user(username='cashier_t',password='TestPass@12345')
        self.client.force_login(u)
        response = self.client.get(reverse('transport:home'))
        self.assertEqual(response.status_code, 403)  # Phase 4B denies non-transport Retail cashiers.
