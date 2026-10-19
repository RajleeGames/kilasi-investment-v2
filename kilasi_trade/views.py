"""Genji and Top-Up screens inherit the existing Novman base, CSS and JS."""
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.models import Product, StockBatch
from core.novman_branch import current_branch, is_admin
from .forms import GenjiForm, TopupForm, UsedSaleForm, UsedStockEditForm
from .models import GenjiSale, TopupTransaction, UsedTopupStock, UsedTopupSale
from .services import post_topup, sell_used_stock

ZERO = Decimal('0.00')


def retail_branch(request):
    if not (is_admin(request.user) or getattr(getattr(request.user, 'profile', None), 'role', '') == 'cashier'):
        raise PermissionDenied('Retail access required.')
    return current_branch(request)


@login_required
def overview(request):
    branch=retail_branch(request)
    genji=GenjiSale.objects.filter(branch=branch,status='completed')
    topups=TopupTransaction.objects.filter(branch=branch,status='completed')
    used_sales=UsedTopupSale.objects.filter(branch=branch)
    genji_revenue=sum((x.total_selling for x in genji), ZERO)
    genji_cost=sum((x.total_buying for x in genji), ZERO)
    exchange_cash=topups.aggregate(v=Sum('addon_amount'))['v'] or ZERO
    exchanged_value=topups.aggregate(v=Sum('used_item_buying_price'))['v'] or ZERO
    exchange_cogs=topups.aggregate(v=Sum('outgoing_cogs'))['v'] or ZERO
    used_revenue=used_sales.aggregate(v=Sum('sold_price'))['v'] or ZERO
    used_cost=sum((x.used_stock.buying_price for x in used_sales.select_related('used_stock')),ZERO)
    return render(request, 'kilasi_trade/overview.html', {
        'branch':branch,'genji_count':genji.count(),'genji_revenue':genji_revenue,'genji_profit':genji_revenue-genji_cost,
        'topup_count':topups.count(),'exchange_cash':exchange_cash,'exchange_value':exchanged_value,
        'exchange_profit':exchange_cash+exchanged_value-exchange_cogs,
        'used_available':UsedTopupStock.objects.filter(branch=branch,status='available').count(),
        'used_revenue':used_revenue,'used_profit':used_revenue-used_cost,
        'total_trade_profit':genji_revenue-genji_cost+exchange_cash+exchanged_value-exchange_cogs+used_revenue-used_cost,
    })


@login_required
def genji_list(request):
    branch=retail_branch(request)
    q=request.GET.get('q','').strip()[:120]
    qs=GenjiSale.objects.filter(branch=branch)
    if q:qs=qs.filter(Q(item_name__icontains=q)|Q(customer_name__icontains=q)|Q(customer_phone__icontains=q))
    return render(request,'kilasi_trade/genji_list.html',{'branch':branch,'rows':qs[:200],'q':q})


@login_required
def genji_new(request):
    branch=retail_branch(request)
    form=GenjiForm(request.POST or None, initial={'buy_date':timezone.localdate(),'sell_date':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.branch=branch;obj.created_by=request.user;obj.save()
        messages.success(request,f'Genji #{obj.pk} saved. No normal stock was deducted.')
        return redirect('kilasi_trade:genji_list')
    return render(request,'kilasi_trade/simple_form.html',{'branch':branch,'form':form,'heading':'New Genji transaction',
        'description':'Record a separate purchased-and-sold item. Prices are per piece; profit is calculated from quantity.','back':'kilasi_trade:genji_list'})


@login_required
@require_POST
def genji_void(request,pk):
    branch=retail_branch(request)
    if not is_admin(request.user): raise PermissionDenied('Administrator approval required.')
    reason=(request.POST.get('reason') or '').strip()
    if len(reason)<5:
        messages.error(request,'Enter a correction reason (at least 5 characters).')
        return redirect('kilasi_trade:genji_list')
    with transaction.atomic():
        obj=get_object_or_404(GenjiSale.objects.select_for_update(),pk=pk,branch=branch)
        if obj.status=='completed':
            obj.status='voided';obj.void_reason=reason[:240];obj.voided_at=timezone.now();obj.save(update_fields=['status','void_reason','voided_at'])
            messages.success(request,'Genji transaction voided; original record preserved.')
        else:messages.error(request,'Transaction is already voided.')
    return redirect('kilasi_trade:genji_list')


@login_required
def topup_list(request):
    branch=retail_branch(request)
    q=(request.GET.get('q') or '').strip()[:120]
    qs=TopupTransaction.objects.filter(branch=branch).select_related('new_product','customer','used_stock')
    if q: qs=qs.filter(Q(used_item_name__icontains=q)|Q(new_product__name__icontains=q)|Q(customer_name__icontains=q))
    return render(request,'kilasi_trade/topup_list.html',{'branch':branch,'rows':qs[:200],'q':q})


@login_required
def topup_new(request):
    branch=retail_branch(request)
    form=TopupForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:
            obj=post_topup(branch=branch, product_id=d['product'].pk, quantity=d['quantity'],
                used_name=d['used_item_name'], category=d['used_item_category'],condition=d['used_item_condition'],
                used_cost=d['used_item_buying_price'],cash_added=d['addon_amount'],
                customer_name=d['customer_name'],customer_phone=d['customer_phone'], note=d['note'],user=request.user)
        except (ValidationError, ValueError, Product.DoesNotExist) as e:
            form.add_error(None, e)
        except Exception as e:
            from core.services import StockError
            if isinstance(e,StockError):form.add_error(None,str(e))
            else:raise
        else:
            messages.success(request,f'Top-up #{obj.pk} posted. Shop stock deducted exactly once; used item received.')
            return redirect('kilasi_trade:topup_list')
    return render(request,'kilasi_trade/simple_form.html',{'branch':branch,'form':form,'heading':'New Top-Up / Exchange',
        'description':'Swap a new product for a used item plus cash. This POSTS one FIFO stock reduction; it is not a normal POS sale.',
        'back':'kilasi_trade:topup_list'})


@login_required
def used_stock_list(request):
    branch=retail_branch(request)
    status=(request.GET.get('status') or '').strip()
    qs=UsedTopupStock.objects.filter(branch=branch).select_related('transaction')
    if status in ('available','sold'):qs=qs.filter(status=status)
    return render(request,'kilasi_trade/used_stock.html',{'branch':branch,'rows':qs[:200],'status':status})


@login_required
def used_sale_new(request,pk):
    branch=retail_branch(request)
    item=get_object_or_404(UsedTopupStock,pk=pk,branch=branch)
    if item.status!='available':
        messages.error(request,'This item has already been sold.')
        return redirect('kilasi_trade:used_stock')
    form=UsedSaleForm(request.POST or None, initial={'sold_price':item.asking_price})
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:
            sale=sell_used_stock(branch=branch,item_id=item.pk,sold_price=d['sold_price'],
                customer_name=d['customer_name'],customer_phone=d['customer_phone'],note=d['note'],user=request.user)
        except (ValidationError,UsedTopupStock.DoesNotExist) as e:
            form.add_error(None,e)
        else:
            messages.success(request,f'Used item sold (sale #{sale.pk}). Stock cannot be sold twice.')
            return redirect('kilasi_trade:used_stock')
    return render(request,'kilasi_trade/simple_form.html',{'branch':branch,'form':form,'heading':f'Sell used item — {item.item_name}',
       'description':f'Original accepted value: {item.buying_price} TZS. Enter the resale price.',
       'back':'kilasi_trade:used_stock'})


@login_required
def used_stock_edit(request,pk):
    branch=retail_branch(request)
    item=get_object_or_404(UsedTopupStock,pk=pk,branch=branch)
    if item.status!='available':
        raise PermissionDenied('Sold used items cannot be edited.')
    form=UsedStockEditForm(request.POST or None,instance=item)
    if request.method=='POST' and form.is_valid():
        # Only descriptive fields and optional asking price are modifiable.
        form.save()
        messages.success(request,'Used-item details updated. Accepted buying value preserved.')
        return redirect('kilasi_trade:used_stock')
    return render(request,'kilasi_trade/simple_form.html',{'branch':branch,'form':form,
        'heading':f'Edit used item — {item.item_name}',
        'description':'Edit condition, asking price, and notes only. The original accepted value cannot be changed.',
        'back':'kilasi_trade:used_stock'})
