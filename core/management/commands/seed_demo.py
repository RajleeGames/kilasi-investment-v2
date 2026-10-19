from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import Category, Product, ProductUnit, ShopSettings
from core.services import add_opening_stock

class Command(BaseCommand):
    help='Create Mega Fish Point demo users and sample products.'
    def handle(self,*args,**opts):
        shop=ShopSettings.get_solo(); shop.name='Mega Fish Point'; shop.address='Moshi, Tanzania'; shop.phone=''; shop.save()
        admin,_=User.objects.get_or_create(username='admin',defaults={'first_name':'Mega','last_name':'Admin','is_staff':True,'is_superuser':True})
        admin.set_password('Mega@12345'); admin.is_staff=True; admin.is_superuser=True; admin.save(); admin.profile.role='admin'; admin.profile.save()
        cashier,_=User.objects.get_or_create(username='cashier',defaults={'first_name':'Cashier'})
        cashier.set_password('Cashier@12345'); cashier.save(); cashier.profile.role='cashier'; cashier.profile.save()
        cats={}
        for name in ['Fish','Chicken','Eggs','Dairy','Dagaa & Nuts']:
            cats[name],_=Category.objects.get_or_create(name=name)
        samples=[
            ('Kibua','Fish','KG',[('KG','KG','1','8000',True)],'18','6500'),
            ('Sato','Fish','KG',[('KG','KG','1','10000',True)],'12.5','7800'),
            ('Eggs','Eggs','PCS',[('Piece','PCS','1','350',True),('Tray','TRAY','30','9500',False)],'90','283.333333'),
            ('Sour Milk','Dairy','ML',[('250 ML','250ML','250','500',False),('500 ML','500ML','500','1000',True),('1 Litre','1L','1000','1800',False)],'20000','1.2'),
            ('Whole Chicken','Chicken','PCS',[('Piece','PCS','1','12000',True)],'15','9500'),
            ('Dagaa','Dagaa & Nuts','KG',[('KG','KG','1','12000',True)],'8','9000'),
            ('Korosho','Dagaa & Nuts','KG',[('KG','KG','1','18000',True)],'5','14000'),
        ]
        for name,cat,base,units,opening,cost in samples:
            p,created=Product.objects.get_or_create(name=name,defaults={'category':cats[cat],'base_unit':base,'low_stock_level':Decimal('3')})
            for un,sym,conv,price,default in units:
                ProductUnit.objects.get_or_create(product=p,name=un,defaults={'symbol':sym,'conversion_to_base':Decimal(conv),'selling_price':Decimal(price),'is_default':default})
            if created: add_opening_stock(product=p,quantity_base=opening,unit_cost_base=cost,reference='Demo opening stock',user=admin)
        self.stdout.write(self.style.SUCCESS('Demo ready. admin/Mega@12345 and cashier/Cashier@12345'))
