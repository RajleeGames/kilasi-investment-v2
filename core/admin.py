from django.contrib import admin
from .models import *
admin.site.register([ShopSettings,UserProfile,Category,Product,ProductUnit,Purchase,PurchaseItem,StockBatch,Sale,SaleItem,SaleAllocation,StockAdjustment,Expense,Customer,CustomerPayment,CashClosing,Stocktake,StocktakeLine])
