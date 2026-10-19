from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from core.models import Branch


class NovmanCashierDashboardTests(TestCase):
    def setUp(self):
        self.majengo = Branch.objects.create(name='Majengo stage3 test', is_active=True)
        self.cashier = User.objects.create_user('cashier_stage3', password='TestPwd_1234')
        self.cashier.profile.role = 'cashier'
        self.cashier.profile.branch = self.majengo
        self.cashier.profile.save(update_fields=['role', 'branch'])
        self.admin = User.objects.create_superuser('admin_stage3', 'a@example.com', 'TestPwd_1234')

    def test_cashier_home_is_redirected_to_pos(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, reverse('pos'), fetch_redirect_response=False)
        response = self.client.get(reverse('dashboard') + '?start=2026-10-01')
        self.assertEqual(response.status_code, 302)

    def test_cashier_login_redirects_to_pos(self):
        response = self.client.post(reverse('login'), {'username': 'cashier_stage3', 'password': 'TestPwd_1234'})
        self.assertRedirects(response, reverse('pos'), fetch_redirect_response=False)

    def test_cashier_pos_sidebar_hides_admin_dashboard(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse('pos'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'href="/" title="Dashboard"')
        self.assertNotContains(response, '<span>Dashboard</span></a>')
        self.assertContains(response, 'Point of Sale')

    def test_cashier_cannot_see_admin_reports_or_branch_switch(self):
        self.client.force_login(self.cashier)
        self.assertRedirects(self.client.get(reverse('reports')), reverse('pos'), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('branch_switch'), {'branch_id': self.majengo.pk}), reverse('pos'), fetch_redirect_response=False)

    def test_admin_can_still_use_dashboard(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sales revenue')
