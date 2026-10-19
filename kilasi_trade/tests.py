from decimal import Decimal
from datetime import date
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from core.models import Branch, Category, Product, StockBatch
from .models import GenjiSale, TopupTransaction, TopupConsumption, UsedTopupStock, UsedTopupSale
from .services import post_topup, sell_used_stock


class TradePostingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('trade_test_admin', password='test123456')
        self.a=Branch.objects.create(name='Phase6A A')
        self.b=Branch.objects.create(name='Phase6A B')
        c=Category.objects.create(name='Phase6A Electronics')
        self.p=Product.objects.create(name='Phase6A Test TV',category=c,base_unit='PCS',is_active=True)
        self.stock_a=StockBatch.objects.create(product=self.p,branch=self.a,source='OPENING',
            quantity_received_base=3,quantity_remaining_base=3,unit_cost_base=Decimal('100.00'),created_by=self.user)
        self.stock_b=StockBatch.objects.create(product=self.p,branch=self.b,source='OPENING',
            quantity_received_base=5,quantity_remaining_base=5,unit_cost_base=Decimal('150.00'),created_by=self.user)

    def post(self, branch=None, quantity=1):
        return post_topup(branch=branch or self.a,product_id=self.p.pk, quantity=quantity,
            used_name='Old TV',category='electronics',condition='Fair',used_cost='70.00',cash_added='40.00', user=self.user)

    def test_exchange_depletes_only_its_branch_and_retains_fifo_cost(self):
        tx=self.post()
        self.stock_a.refresh_from_db();self.stock_b.refresh_from_db()
        self.assertEqual(self.stock_a.quantity_remaining_base,Decimal('2'))
        self.assertEqual(self.stock_b.quantity_remaining_base,Decimal('5'))
        self.assertEqual(tx.outgoing_cogs,Decimal('100.00'))
        self.assertEqual(tx.exchange_profit,Decimal('10.00'))
        self.assertEqual(tx.fifo_allocations.count(),1)
        self.assertEqual(UsedTopupStock.objects.filter(transaction=tx).count(),1)

    def test_insufficient_stock_rolls_back_everything(self):
        with self.assertRaises(Exception):self.post(quantity=4)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.quantity_remaining_base,Decimal('3'))
        self.assertEqual(TopupTransaction.objects.count(),0)
        self.assertEqual(UsedTopupStock.objects.count(),0)

    def test_used_item_sells_once(self):
        tx=self.post()
        used=tx.used_stock
        sale=sell_used_stock(branch=self.a,item_id=used.pk,sold_price='90',user=self.user)
        self.assertEqual(sale.profit, Decimal('20.00'))
        with self.assertRaises(ValidationError):sell_used_stock(branch=self.a,item_id=used.pk,sold_price='100',user=self.user)
        self.assertEqual(UsedTopupSale.objects.count(),1)

    def test_reject_cross_branch_resale(self):
        used=self.post().used_stock
        with self.assertRaises(UsedTopupStock.DoesNotExist):
            sell_used_stock(branch=self.b,item_id=used.pk,sold_price='90',user=self.user)

    def test_genji_profit_uses_quantity_and_is_independent(self):
        obj=GenjiSale.objects.create(branch=self.a,item_name='Standalone chair',buy_date=date(2026,10,1),
            sell_date=date(2026,10,2),quantity=2,buying_cost=30,selling_price=50,created_by=self.user)
        self.assertEqual(obj.profit,Decimal('40'))
        self.assertEqual(TopupTransaction.objects.count(),0)
