"""Run python manage.py test kilasi_transport.test_phase4b"""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from core.models import Branch
from .models import Booking, Driver, TransportAccess, Trip, TripPayment, Vehicle


class TransportAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.admin = U.objects.create_superuser('branch_admin', 'admin@example.test', 'SafePass@2026')
        cls.a = Branch.objects.create(name='Kilasi TR A')
        cls.b = Branch.objects.create(name='Kilasi TR B')
        cls.manager = U.objects.create_user('transport_manager_test', password='SafePass@2026')
        cls.staff = U.objects.create_user('transport_staff_test', password='SafePass@2026')
        for u,role in ((cls.manager,'manager'),(cls.staff,'staff')):
            u.profile.branch=cls.a
            u.profile.role='cashier'
            u.profile.save(update_fields=['branch','role'])
            TransportAccess.objects.create(user=u, role=role, branch=cls.a)
        cls.v = Vehicle.objects.create(registration='TZ 555 ZZZ', make_model='Toyota Truck')
        cls.d = Driver.objects.create(full_name='Staff Test Driver')
        now=timezone.now()+timedelta(days=2)
        cls.trip = Trip.objects.create(branch=cls.a, vehicle=cls.v, driver=cls.d,
            customer_name='Transport Customer', origin='Moshi', destination='Arusha',
            scheduled_start=now, scheduled_end=now+timedelta(hours=4),
            agreed_fee=Decimal('987654.00'), created_by=cls.admin)
        cls.other = Trip.objects.create(branch=cls.b, vehicle=cls.v, driver=cls.d,
            customer_name='Secret Branch B', origin='Singida', destination='Dodoma',
            scheduled_start=now+timedelta(days=3), scheduled_end=now+timedelta(days=3,hours=4),
            agreed_fee=Decimal('333333.00'), created_by=cls.admin)

    def test_login_redirects_transport_user(self):
        resp=self.client.post(reverse('login'),{'username':'transport_staff_test','password':'SafePass@2026'})
        self.assertRedirects(resp, reverse('transport:home'),fetch_redirect_response=False)

    def test_staff_can_view_operations_not_retail_or_finance(self):
        self.client.force_login(self.staff)
        for route in (reverse('transport:home'),reverse('transport:vehicles'),
                      reverse('transport:drivers'),reverse('transport:trips'),
                      reverse('transport:bookings'),reverse('transport:trip_detail',args=[self.trip.pk])):
            self.assertEqual(self.client.get(route).status_code,200,route)
        for route in (reverse('pos'),reverse('dashboard'),reverse('transport:reports'),
                      reverse('transport:expenses'),reverse('transport:payment_new',args=[self.trip.pk]),
                      reverse('transport:trip_new'),reverse('transport:vehicle_new'),reverse('transport:users')):
            self.assertEqual(self.client.get(route).status_code,403,route)
        self.assertEqual(self.client.get('/sms/').status_code,403)
        self.assertEqual(self.client.get('/django-admin/').status_code,403)
        resp=self.client.get(reverse('transport:trips'))
        self.assertNotContains(resp,'987,654')
        self.assertNotContains(resp,'333,333')
        resp=self.client.get(reverse('transport:trip_detail',args=[self.trip.pk]))
        self.assertNotContains(resp,'987,654')

    def test_staff_can_create_booking_but_not_edit_existing(self):
        self.client.force_login(self.staff)
        data={'customer_name':'Test Client','customer_phone':'0700112233',
              'origin':'Moshi','destination':'Arusha','requested_date':timezone.localdate().isoformat(),
              'cargo_details':'Small load','notes':'Test only','quoted_amount':'1000000','trip':str(self.trip.pk)}
        resp=self.client.post(reverse('transport:booking_new'),data)
        self.assertEqual(resp.status_code,302)
        b=Booking.objects.get(customer_name='Test Client')
        self.assertIsNone(b.branch_id)
        self.assertEqual(b.quoted_amount,Decimal('0'))
        self.assertIsNone(b.trip_id)
        self.assertEqual(self.client.get(reverse('transport:booking_edit',args=[b.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('transport:booking_status',args=[b.pk]),{'status':'confirmed'}).status_code,403)

    def test_staff_cannot_post_financial_updates(self):
        self.client.force_login(self.staff)
        before=TripPayment.objects.count()
        resp=self.client.post(reverse('transport:payment_new',args=[self.trip.pk]),{'amount':'500','method':'cash'})
        self.assertEqual(resp.status_code,403)
        self.assertEqual(TripPayment.objects.count(),before)

    def test_manager_can_operate_independent_of_retail_branches(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('transport:reports')).status_code,200)
        self.assertEqual(self.client.get(reverse('transport:expenses')).status_code,200)
        self.assertEqual(self.client.get(reverse('pos')).status_code,403)
        self.assertEqual(self.client.get(reverse('transport:trip_detail',args=[self.other.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('transport:users')).status_code,403)

    def test_suspended_membership_rejects_transport(self):
        self.client.force_login(self.staff)
        access=self.staff.transport_access
        access.is_active=False
        access.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('transport:home')).status_code,403)
        self.assertEqual(self.client.get(reverse('pos')).status_code,403)

    def test_admin_can_create_and_suspend_transport_account(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('transport:users')).status_code,200)
        response=self.client.post(reverse('transport:users'),{
            'username':'new_dispatcher_test','first_name':'New','last_name':'Staff',
            'password':'SafePass@2026','role':'staff','branch':str(self.b.pk)})
        self.assertEqual(response.status_code,302)
        account=TransportAccess.objects.get(user__username='new_dispatcher_test')
        self.assertIsNone(account.branch_id)
        self.assertIsNone(account.user.profile.branch_id)
        self.assertEqual(account.user.profile.role,'cashier')
        self.assertFalse(account.user.is_superuser)
        r=self.client.post(reverse('transport:user_edit',args=[account.pk]),{
            'role':'manager','branch':str(self.b.pk),'new_password':''})
        self.assertEqual(r.status_code,302)
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertFalse(account.user.is_active)
