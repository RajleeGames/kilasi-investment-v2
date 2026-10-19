"""Service-level accounting tests; run with manage.py test kilasi_orders."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from core.models import Branch, Category, Product, ProductUnit, Sale, SaleItem, SaleAllocation, StockBatch
from .models import CustomerOrder, OrderPayment, SalesReturnLine, ReturnStockAllocation
from .services import take_order_payment, void_order_payment, change_order_status, post_sale_return

class OrderAndReturnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=get_user_model().objects.create_user(username='kilasi_order_test_admin',password='unimportant-nonproduction')
        cls.branch=Branch.objects.create(name='Orders Test Branch')
        cls.other=Branch.objects.create(name='Other Test Branch')
        cls.category=Category.objects.create(name='Order Test Category')
        cls.product=Product.objects.create(name='Order Test Table',category=cls.category,base_unit='PCS')
        cls.unit=ProductUnit.objects.create(product=cls.product,name='Piece',symbol='Pc',conversion_to_base=1,selling_price=Decimal('100'))
    def make_order(self):
        return CustomerOrder.objects.create(branch=self.branch,customer_name='Test Customer',custom_product_name='Test Sofa',
            description='Made to order',quantity=1,total_amount=Decimal('250000'),created_by=self.user)
    def test_partial_payment_balance_and_limit(self):
        order=self.make_order()
        take_order_payment(order_id=order.pk,branch=self.branch,amount=Decimal('50000'),method='cash',user=self.user)
        self.assertEqual(order.paid_amount,Decimal('50000'))
        self.assertEqual(order.balance,Decimal('200000'))
        self.assertEqual(order.payment_status,'Partial')
        with self.assertRaises(ValidationError):take_order_payment(order_id=order.pk,branch=self.branch,amount=Decimal('200001'),method='cash',user=self.user)
        self.assertEqual(order.payments.count(),1)
    def test_order_payment_void_keeps_audit(self):
        order=self.make_order()
        p=take_order_payment(order_id=order.pk,branch=self.branch,amount=Decimal('100'),method='bank',user=self.user)
        void_order_payment(pk=p.pk,branch=self.branch,user=self.user,reason='Wrong deposit')
        self.assertEqual(order.balance,order.total_amount)
        p.refresh_from_db();self.assertTrue(p.is_voided)
        self.assertEqual(order.payments.count(),1)
        with self.assertRaises(ValidationError):void_order_payment(pk=p.pk,branch=self.branch,user=self.user,reason='Repeat void')
    def test_cancel_order_with_deposit_rejected(self):
        order=self.make_order()
        take_order_payment(order_id=order.pk,branch=self.branch,amount=Decimal('100'),method='cash',user=self.user)
        with self.assertRaises(ValidationError):change_order_status(order_id=order.pk,branch=self.branch,status='cancelled')
    def test_branch_payment_isolation(self):
        order=self.make_order()
        with self.assertRaises(CustomerOrder.DoesNotExist):take_order_payment(order_id=order.pk,branch=self.other,amount=Decimal('100'),method='cash',user=self.user)
    def make_sale(self,payment_method='cash'):
        batch=StockBatch.objects.create(product=self.product,branch=self.branch,source='OPENING',reference='Test opening',
            quantity_received_base=Decimal('5'),quantity_remaining_base=Decimal('3'),unit_cost_base=Decimal('40'),created_by=self.user)
        sale=Sale.objects.create(branch=self.branch,payment_method=payment_method,cashier=self.user,
            subtotal=Decimal('200'),grand_total=Decimal('200'),cogs_total=Decimal('80'),profit_total=Decimal('120'))
        item=SaleItem.objects.create(sale=sale,product=self.product,unit=self.unit,quantity=Decimal('2'),
            conversion_to_base=Decimal('1'),base_quantity=Decimal('2'),normal_unit_price=Decimal('100'),
            line_subtotal=Decimal('200'),discount_amount=Decimal('0'),final_total=Decimal('200'),cost_total=Decimal('80'))
        allocation=SaleAllocation.objects.create(sale_item=item,batch=batch,quantity_base=Decimal('2'),unit_cost_base=Decimal('40'),cost_total=Decimal('80'))
        return sale,item,batch,allocation
    def test_partial_returns_restore_original_cost_once(self):
        sale,item,batch,allocation=self.make_sale()
        args=dict(branch=self.branch,sale_id=sale.pk,sale_item_id=item.pk,quantity=Decimal('1'),reason='Damaged packaging',
                  restock=True,method='cash',reference='',user=self.user)
        ret=post_sale_return(**args)
        self.assertEqual(ret.total_refund,Decimal('100'))
        self.assertEqual(ret.stock_cost_restored,Decimal('40'))
        self.assertEqual(ReturnStockAllocation.objects.filter(original_allocation=allocation).count(),1)
        post_sale_return(**args)
        self.assertEqual(ReturnStockAllocation.objects.filter(original_allocation=allocation).count(),2)
        self.assertEqual(sum((x.quantity_base for x in ReturnStockAllocation.objects.filter(original_allocation=allocation)),Decimal('0')),Decimal('2'))
        self.assertEqual(SalesReturnLine.objects.filter(sale_item=item).count(),2)
        with self.assertRaises(ValidationError):post_sale_return(**args)
        self.assertEqual(SalesReturnLine.objects.filter(sale_item=item).count(),2)
    def test_no_restock_preserves_fifo_balance(self):
        sale,item,batch,allocation=self.make_sale()
        ret=post_sale_return(branch=self.branch,sale_id=sale.pk,sale_item_id=item.pk,quantity=Decimal('1'),
            reason='Broken beyond resale',restock=False,method='cash',reference='',user=self.user)
        self.assertEqual(ret.stock_cost_restored,Decimal('0'))
        self.assertEqual(StockBatch.objects.filter(branch=self.branch,product=self.product).count(),1)
    def test_debt_sales_return_blocked(self):
        sale,item,batch,allocation=self.make_sale(payment_method='debt')
        with self.assertRaises(ValidationError):post_sale_return(branch=self.branch,sale_id=sale.pk,sale_item_id=item.pk,quantity=Decimal('1'),
            reason='Changed their mind',restock=True,method='cash',reference='',user=self.user)
        self.assertEqual(SalesReturnLine.objects.filter(sale_item=item).count(),0)
