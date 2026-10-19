"""Safety checks for Novman branch-scoped dashboard and existing chart UI."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (Branch, Category, Customer, CustomerPayment, Expense,
                         Product, Sale, SaleItem, StockBatch)


class NovmanDashboardV3Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('v3_dashboard_admin', 'admin@example.invalid', 'Test-Pass-1234')
        self.majengo = Branch.objects.create(name='Majengo V3', is_active=True)
        self.sironga = Branch.objects.create(name='Sironga V3', is_active=True)
        self.category = Category.objects.create(name='Bike Parts')
        self.part = Product.objects.create(name='Chain Kit', category=self.category, base_unit='PCS')
        StockBatch.objects.create(
            product=self.part, branch=self.majengo, source='OPENING',
            quantity_received_base=Decimal('5'), quantity_remaining_base=Decimal('5'),
            unit_cost_base=Decimal('20'), created_by=self.admin,
        )
        StockBatch.objects.create(
            product=self.part, branch=self.sironga, source='OPENING',
            quantity_received_base=Decimal('8'), quantity_remaining_base=Decimal('8'),
            unit_cost_base=Decimal('900'), created_by=self.admin,
        )
        now = timezone.now()
        self.majengo_sale = Sale.objects.create(
            branch=self.majengo, cashier=self.admin, sold_at=now,
            status='completed', payment_method='cash',
            grand_total=Decimal('300'), cogs_total=Decimal('150'),
            profit_total=Decimal('150'),
        )
        self.sironga_sale = Sale.objects.create(
            branch=self.sironga, cashier=self.admin, sold_at=now,
            status='completed', payment_method='bank',
            grand_total=Decimal('2000'), cogs_total=Decimal('1200'),
            profit_total=Decimal('800'),
        )
        self.customer = Customer.objects.create(name='Dashboard customer')
        self.debt_sale = Sale.objects.create(
            branch=self.majengo, cashier=self.admin, customer=self.customer, sold_at=now,
            status='completed', payment_method='debt',
            grand_total=Decimal('50'), cogs_total=Decimal('25'), profit_total=Decimal('25'),
        )
        CustomerPayment.objects.create(
            branch=self.majengo, customer=self.customer, amount=Decimal('20'),
            method='cash', received_by=self.admin,
        )
        Expense.objects.create(
            branch=self.majengo, description='Fuel', category='fuel',
            amount=Decimal('25'), expense_date=timezone.localdate(), created_by=self.admin,
        )
        Expense.objects.create(
            branch=self.sironga, description='Other branch expense', category='fuel',
            amount=Decimal('300'), expense_date=timezone.localdate(), created_by=self.admin,
        )
        self.client.force_login(self.admin)

    def select_branch(self, branch):
        session = self.client.session
        session['novman_branch_id'] = branch.pk
        session.save()

    def test_chart_ids_exist_and_data_is_from_selected_branch_only(self):
        self.select_branch(self.majengo)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'salesTrendChart')
        self.assertContains(response, 'paymentDonutChart')
        self.assertContains(response, 'dashboard-data')
        self.assertEqual(response.context['revenue'], Decimal('350'))
        self.assertEqual(response.context['gross_profit'], Decimal('175'))
        self.assertEqual(response.context['net_today'], Decimal('150'))
        self.assertEqual(response.context['outstanding_debt'], Decimal('30'))
        self.assertEqual(response.context['stock_value'], Decimal('100'))
        self.assertEqual(response.context['stock_qty'], Decimal('5'))
        values = response.context['dashboard_chart_data']
        self.assertAlmostEqual(sum(row['revenue'] for row in values['daily']), 350)
        payment = {p['key']: p['value'] for p in values['payments']}
        self.assertEqual(payment['cash'], 300)
        self.assertEqual(payment['bank'], 0)
        self.assertEqual(payment['debt'], 50)
        self.assertNotContains(response, self.sironga_sale.receipt_no)

    def test_switching_branches_changes_all_business_amounts(self):
        self.select_branch(self.sironga)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['revenue'], Decimal('2000'))
        self.assertEqual(response.context['gross_profit'], Decimal('800'))
        self.assertEqual(response.context['expenses_today'], Decimal('300'))
        self.assertEqual(response.context['stock_value'], Decimal('7200'))
        self.assertEqual(response.context['stock_qty'], Decimal('8'))
        self.assertEqual(response.context['outstanding_debt'], Decimal('0'))
        self.assertContains(response, self.sironga_sale.receipt_no)
        self.assertNotContains(response, self.majengo_sale.receipt_no)

    def test_large_date_range_is_capped_and_invalid_dates_do_not_crash(self):
        self.select_branch(self.majengo)
        response = self.client.get(reverse('dashboard') + '?start=2000-01-01&end=2099-01-01')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['dashboard_chart_data']['daily']), 366)
        self.assertTrue(response.context['range_limited'])
        other = self.client.get(reverse('dashboard') + '?start=not-a-date&end=invalid')
        self.assertEqual(other.status_code, 200)
        self.assertEqual(len(other.context['dashboard_chart_data']['daily']), 1)

    def test_cashier_cannot_open_dashboard(self):
        cashier = User.objects.create_user('novman_v3_cashier', password='Local-Test-1234')
        cashier.profile.role = 'cashier'
        cashier.profile.branch = self.majengo
        cashier.profile.save(update_fields=['role', 'branch'])
        self.client.force_login(cashier)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('pos'))
