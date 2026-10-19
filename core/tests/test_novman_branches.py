from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from core.models import Branch, Category, Product, ProductUnit, StockBatch, StockTransfer, Sale
from core.novman_stock import branch_quantity, sell, transfer
from core.services import StockError, void_sale


class NovmanBranchSafetyTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('branchadmin','test@example.invalid','Temp!Password1234')
        self.majengo=Branch.objects.create(name='SHAYO MAJENGO SPARE PARTS',legacy_novman_id=2)
        self.sironga=Branch.objects.create(name='SIRONGA SPARE PARTS',legacy_novman_id=3)
        cat=Category.objects.create(name='Brake parts')
        self.p=Product.objects.create(name='Brake Pad',base_unit='PCS',category=cat)
        self.other=Product.objects.create(name='Brake Pad',base_unit='PCS',category=cat)
        self.unit=ProductUnit.objects.create(product=self.p,name='Piece',symbol='PCS',conversion_to_base=1,
            selling_price=Decimal('20000'),is_default=True)
        StockBatch.objects.create(product=self.p,branch=self.majengo,source='OPENING',reference='Test',
            quantity_received_base=5,quantity_remaining_base=5,unit_cost_base=Decimal('12000'),created_by=self.admin)

    def test_same_name_products_are_separate(self):
        self.assertEqual(Product.objects.filter(name='Brake Pad').count(),2)

    def test_empty_branch_cannot_sell_majengo_stock(self):
        with self.assertRaises(StockError):
            sell(branch=self.sironga,cart=[{'product_id':self.p.pk,'unit_id':self.unit.pk,'quantity':'1'}],
                 payment_method='cash',customer_name='',customer_phone='',sale_discount=0,cashier=self.admin)
        self.assertEqual(Sale.objects.count(),0)
        self.assertEqual(branch_quantity(self.p,self.majengo),5)
        self.assertEqual(branch_quantity(self.p,self.sironga),0)

    def test_transfer_preserves_fifo_cost(self):
        tr=transfer(source=self.majengo,destination=self.sironga,product=self.p,quantity=2,user=self.admin)
        self.assertEqual(tr.quantity_base,2)
        self.assertEqual(branch_quantity(self.p,self.majengo),3)
        self.assertEqual(branch_quantity(self.p,self.sironga),2)
        self.assertEqual(tr.lines.first().unit_cost_base,Decimal('12000'))

    def test_sell_and_void_restore_correct_branch(self):
        sale=sell(branch=self.majengo,
            cart=[{'product_id':self.p.pk,'unit_id':self.unit.pk,'quantity':'2','discount':'0'}],
            payment_method='cash',customer_name='',customer_phone='',sale_discount=0,cashier=self.admin)
        self.assertEqual(sale.branch_id,self.majengo.pk)
        self.assertEqual(sale.grand_total,Decimal('40000'))
        self.assertEqual(sale.cogs_total,Decimal('24000'))
        self.assertEqual(branch_quantity(self.p,self.majengo),3)
        self.assertEqual(branch_quantity(self.p,self.sironga),0)
        void_sale(sale=sale,user=self.admin,reason='Test reversal')
        self.assertEqual(branch_quantity(self.p,self.majengo),5)
        self.assertEqual(branch_quantity(self.p,self.sironga),0)
