"""Run billing tests with the project virtual-environment Python."""
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from core.models import Branch, Customer, CustomerPayment, Sale
from .models import DeliveryNote, Invoice, InvoiceAllocation, Proforma, ProformaLine


class BillingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='billing_admin', email='', password='local-test-only')
        cls.branch = Branch.objects.create(name='Main Store')
        cls.other = Branch.objects.create(name='Other Branch')
        cls.customer = Customer.objects.create(name='Local Customer')
        cls.sale = Sale.objects.create(branch=cls.branch, customer=cls.customer,
                                       customer_name=cls.customer.name, cashier=cls.user,
                                       payment_method='debt', grand_total=Decimal('100000.00'))
        cls.cash_sale = Sale.objects.create(branch=cls.branch, customer_name='Walk in', cashier=cls.user,
                                            payment_method='cash', grand_total=Decimal('50000.00'))

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['novman_branch_id'] = self.branch.pk
        session.save()

    def test_invoice_created_only_once_without_new_sale(self):
        target = reverse('billing:invoice_new', args=[self.sale.pk])
        before = Sale.objects.count()
        resp = self.client.post(target, {'issue_date':'2026-10-18'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(Sale.objects.count(), before)
        self.assertEqual(Invoice.objects.get().balance, Decimal('100000.00'))
        self.client.post(target, {'issue_date':'2026-10-18'})
        self.assertEqual(Invoice.objects.count(), 1)

    def test_invoice_payment_is_single_core_ledger_entry(self):
        invoice = Invoice.objects.create(sale=self.sale, created_by=self.user)
        resp = self.client.post(reverse('billing:invoice_pay', args=[invoice.pk]),
            {'amount':'25000.00','method':'cash','reference':'TEST-1'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(CustomerPayment.objects.count(), 1)
        self.assertEqual(InvoiceAllocation.objects.count(), 1)
        self.assertEqual(invoice.balance, Decimal('75000.00'))

    def test_no_overpayment(self):
        invoice = Invoice.objects.create(sale=self.sale, created_by=self.user)
        self.client.post(reverse('billing:invoice_pay',args=[invoice.pk]),
                         {'amount':'100001.00','method':'cash'})
        self.assertEqual(CustomerPayment.objects.count(), 0)

    def test_cash_sale_invoiced_as_paid(self):
        inv = Invoice.objects.create(sale=self.cash_sale, created_by=self.user)
        self.assertEqual(inv.balance, Decimal('0.00'))
        self.assertEqual(inv.payment_state, 'Paid')
        self.client.post(reverse('billing:invoice_pay',args=[inv.pk]),
                         {'amount':'50.00','method':'cash'})
        self.assertEqual(CustomerPayment.objects.count(), 0)

    def test_cross_branch_access_denied(self):
        invoice = Invoice.objects.create(sale=self.sale, created_by=self.user)
        session = self.client.session
        session['novman_branch_id'] = self.other.pk
        session.save()
        self.assertEqual(self.client.get(reverse('billing:invoice_detail', args=[invoice.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('billing:invoice_new', args=[self.sale.pk])).status_code, 404)

    def test_proforma_does_not_create_sale(self):
        count = Sale.objects.count()
        doc = Proforma.objects.create(branch=self.branch, customer_name='Quote Customer', created_by=self.user)
        line = ProformaLine.objects.create(proforma=doc, description='Table', quantity=Decimal('2'),
                    unit='PCS', unit_price=Decimal('15000'), discount=Decimal('1000'))
        self.assertEqual(line.total, Decimal('29000.00'))
        self.assertEqual(doc.total, Decimal('29000.00'))
        self.assertEqual(Sale.objects.count(), count)

    def test_delivery_note_does_not_deduct_stock(self):
        before = Sale.objects.count()
        response = self.client.post(reverse('billing:delivery_new', args=[self.sale.pk]),
            {'delivered_to':'Local Customer','issue_date':'2026-10-18'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DeliveryNote.objects.count(), 1)
        self.assertEqual(Sale.objects.count(), before)
