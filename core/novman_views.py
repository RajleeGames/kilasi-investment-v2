"""Branch-aware offline POS endpoints for Novman V2.

All public URLs for stock mutations and finance use these views; the Jane
routes that lack branch awareness are intentionally not used.
"""
import json
from decimal import Decimal
from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import (
    Branch, Product, ProductUnit, StockBatch, StockAdjustment, StockTransfer,
    Sale, SaleItem, Purchase, Expense, CustomerPayment, CashClosing,
    Stocktake, Customer,
)
from .novman_branch import current_branch, is_admin
from . import novman_stock as ops
from .services import D, StockError, record_customer_payment, void_sale
from .decorators import admin_required
from .forms import ExpenseForm, PurchaseMetaForm

ZERO = Decimal('0')


def _date(text, fallback):
    try: return datetime.strptime(text, '%Y-%m-%d').date()
    except (TypeError, ValueError): return fallback


def _money(qs, field):
    return qs.aggregate(v=Sum(field))['v'] or ZERO


@admin_required
@require_POST
def branch_switch(request):
    branch = get_object_or_404(Branch, pk=request.POST.get('branch_id'), is_active=True, name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA'))
    request.session['novman_branch_id'] = branch.pk
    # Redirect to a known safe location, not an untrusted URL.
    return redirect('dashboard')


@admin_required
def dashboard(request):
    from .novman_dashboard_v3 import dashboard_context
    return render(request, 'novman_dashboard.html', dashboard_context(request))

@login_required
def pos(request):
    branch = current_branch(request)
    stock_by_id = dict(StockBatch.objects.filter(branch=branch)
        .values('product_id').annotate(q=Sum('quantity_remaining_base'))
        .values_list('product_id', 'q'))
    products = []
    for p in (Product.objects.filter(is_active=True).select_related('category')
              .prefetch_related('selling_units').order_by('name','id')):
        units = [{'id':u.pk,'name':u.name,'symbol':u.symbol,
                  'conversion':str(u.conversion_to_base),'price':str(u.selling_price),
                  'default':u.is_default} for u in p.selling_units.all() if u.is_active]
        if units:
            products.append({'id':p.pk,'name':p.name,'barcode':p.barcode or '',
                'category':p.category.name,'base_unit':p.base_unit,
                'stock':str(stock_by_id.get(p.pk, ZERO)), 'units':units})
    return render(request,'pos.html',{'products_json':json.dumps(products), 'branch':branch})


@login_required
@require_POST
def pos_checkout(request):
    try:
        branch = current_branch(request)
        sale = ops.sell(branch=branch, cart=json.loads(request.POST.get('cart_json','[]')),
            payment_method=request.POST.get('payment_method','cash'),
            customer_name=request.POST.get('customer_name',''),
            customer_phone=request.POST.get('customer_phone',''),
            sale_discount=request.POST.get('sale_discount','0'),cashier=request.user)
        local = timezone.localtime(sale.sold_at)
        receipt = {'sale_id':sale.id,'receipt_no':sale.receipt_no,
            'date':local.strftime('%d/%m/%Y'),'time':local.strftime('%H:%M:%S'),
            'cashier':request.user.get_full_name().strip() or request.user.username,
            'branch':branch.name,'payment_method':sale.get_payment_method_display(),
            'customer_name':request.POST.get('customer_name',''),
            'customer_phone':request.POST.get('customer_phone',''),
            'items':[{'name':x.product.name,'unit':x.unit.symbol or x.unit.name,
                'quantity':str(x.quantity),'unit_price':str(x.normal_unit_price),
                'discount':str(x.discount_amount),'total':str(x.final_total)}
                for x in sale.items.select_related('product','unit')],
            'subtotal':str(sale.subtotal),'item_discount_total':str(sale.item_discount_total),
            'sale_discount':str(sale.sale_discount),'grand_total':str(sale.grand_total)}
        return JsonResponse({'ok':True,'sale_id':sale.pk,'receipt_no':sale.receipt_no,'receipt':receipt})
    except (StockError, ValueError, TypeError, KeyError, json.JSONDecodeError, Product.DoesNotExist, ProductUnit.DoesNotExist) as exc:
        return JsonResponse({'ok':False,'error':str(exc)},status=400)


@login_required
def sales(request):
    qs = Sale.objects.filter(branch=current_branch(request)).select_related('cashier','customer')
    if not is_admin(request.user): qs = qs.filter(cashier=request.user)
    # KILASI_PHASE9D_RETAIL_PAGER_FIXED_V1_1: filter only the already branch/role-scoped queryset
    from .kilasi_native_paging import sales_page as _kilasi_sales_page
    _kilasi_sales_obj, _kilasi_sales_extra = _kilasi_sales_page(request, qs)
    return render(request,'sales.html',{**({'sales':qs[:150]}), 'sales': _kilasi_sales_obj.object_list, 'page_obj': _kilasi_sales_obj, **_kilasi_sales_extra})


@login_required
def sale_detail(request, pk):
    sale=get_object_or_404(Sale.objects.select_related('cashier','customer')
        .prefetch_related('items__product','items__unit'),pk=pk,branch=current_branch(request))
    if not is_admin(request.user) and sale.cashier_id!=request.user.id:
        return HttpResponseForbidden('This receipt does not belong to you.')
    from django.conf import settings
    return render(request,'sale_detail.html',{'sale':sale,
        'qz_printer_name':getattr(settings,'QZ_PRINTER_NAME','')})


@admin_required
@require_POST
def sale_void(request, pk):
    sale=get_object_or_404(Sale,pk=pk,branch=current_branch(request))
    try:
        void_sale(sale=sale,user=request.user,reason=request.POST.get('reason',''))
        messages.success(request,'Sale voided and stock restored to the original batches.')
    except StockError as exc: messages.error(request,str(exc))
    return redirect('sale_detail',pk=pk)


@admin_required
def products(request):
    branch=current_branch(request)
    q=(request.GET.get('q') or '').strip()
    queryset=Product.objects.select_related('category','supplier').prefetch_related('selling_units').order_by('name','id')
    division=(request.GET.get('division') or '').strip()
    if division not in dict(Product.RETAIL_DIVISION_CHOICES): division=''
    if division: queryset=queryset.filter(retail_division=division)
    if q: queryset=queryset.filter(Q(name__icontains=q)|Q(barcode__icontains=q)|Q(category__name__icontains=q))
    quantities=dict(StockBatch.objects.filter(branch=branch)
        .values('product_id').annotate(q=Sum('quantity_remaining_base')).values_list('product_id','q'))
    rows=[]
    # KILASI_PHASE9D_RETAIL_PAGER_FIXED_V1_1: 40 products per page, original filters and row formatting preserved
    from django.core.paginator import Paginator as _KilasiPaginator
    from .kilasi_native_paging import pagination_links as _kilasi_page_links_fn
    _kilasi_page_obj = _KilasiPaginator(queryset, 40).get_page(request.GET.get('page'))
    _kilasi_page_links = _kilasi_page_links_fn(request, _kilasi_page_obj)
    for p in _kilasi_page_obj.object_list:
        unit=next((u for u in p.selling_units.all() if u.is_default),None)
        if unit is None: unit=next(iter(p.selling_units.all()),None)
        rows.append({'product':p,'quantity':quantities.get(p.pk,ZERO),'unit':unit})
    return render(request,'novman_products.html',{**({'rows':rows,'q':q,'branch':branch,'division':division,'division_choices':Product.RETAIL_DIVISION_CHOICES}), 'page_obj': _kilasi_page_obj, **_kilasi_page_links})


@admin_required
def stock(request):
    branch=current_branch(request)
    q=(request.GET.get('q') or '').strip()
    qs=Product.objects.select_related('category').order_by('name','id')
    division=(request.GET.get('division') or '').strip()
    if division not in dict(Product.RETAIL_DIVISION_CHOICES): division=''
    if division: qs=qs.filter(retail_division=division)
    if q: qs=qs.filter(Q(name__icontains=q)|Q(barcode__icontains=q)|Q(category__name__icontains=q))
    rows=[]
    for p in qs[:1000]:
        batches=list(StockBatch.objects.filter(branch=branch,product=p,quantity_remaining_base__gt=0))
        qty=sum((b.quantity_remaining_base for b in batches),ZERO)
        value=sum((b.quantity_remaining_base*b.unit_cost_base for b in batches),ZERO)
        rows.append({'product':p,'quantity':qty,'value':value,'avg':value/qty if qty else ZERO})
    return render(request,'novman_stock.html',{'rows':rows,'q':q,'branch':branch,'division':division,'division_choices':Product.RETAIL_DIVISION_CHOICES})


@admin_required
def stock_detail(request, pk):
    branch=current_branch(request)
    p=get_object_or_404(Product.objects.select_related('category','supplier'),pk=pk)
    batches=list(StockBatch.objects.filter(branch=branch,product=p)
        .select_related('purchase_item','created_by').order_by('-received_at','-pk'))
    for b in batches:
        b.can_edit_cost=(b.quantity_remaining_base==b.quantity_received_base
                         and not b.sale_allocations.exists())
        b.remaining_value=b.quantity_remaining_base*b.unit_cost_base
    qty=sum((b.quantity_remaining_base for b in batches),ZERO)
    value=sum((b.remaining_value for b in batches),ZERO)
    return render(request,'novman_stock_detail.html',{'product':p,'branch':branch,'batches':batches,
        'current_qty':qty,'current_value':value,'average_buying_cost':value/qty if qty else ZERO})


@admin_required
def stock_batch_cost_edit(request, pk):
    batch=get_object_or_404(StockBatch,pk=pk,branch=current_branch(request))
    if request.method!='POST': return redirect('stock_detail',pk=batch.product_id)
    try:
        value=D(request.POST.get('cost'))
        if value < 0: raise StockError('Buying price must be non-negative.')
        if (batch.legacy_novman_id is not None or batch.source != 'OPENING' or
            batch.reference.startswith(('Adjustment:', 'Transfer #')) or
            batch.quantity_remaining_base != batch.quantity_received_base or batch.sale_allocations.exists()):
            raise StockError('Imported, purchased, transferred or used batches cannot be edited here.')
        batch.unit_cost_base=value
        batch.save(update_fields=['unit_cost_base'])
        messages.success(request,'Unused stock batch cost updated.')
    except StockError as exc: messages.error(request,str(exc))
    return redirect('stock_detail',pk=batch.product_id)


@admin_required
def opening_stock(request):
    branch=current_branch(request)
    products_qs=Product.objects.filter(is_active=True).prefetch_related('selling_units')
    if request.method=='POST':
        product=get_object_or_404(Product,pk=request.POST.get('product'))
        unit=get_object_or_404(ProductUnit,pk=request.POST.get('unit'),product=product)
        try:
            quantity=D(request.POST.get('quantity'))*unit.conversion_to_base
            unit_cost=D(request.POST.get('cost_per_unit','0'))
            if unit.conversion_to_base<=0: raise StockError('Invalid unit conversion.')
            ops.opening_stock(product=product,branch=branch,quantity_base=quantity,
                unit_cost_base=unit_cost/unit.conversion_to_base,
                reference=request.POST.get('reference',''),user=request.user)
            messages.success(request,f'Opening stock added to {branch.name}.')
            return redirect('stock')
        except StockError as exc: messages.error(request,str(exc))
    data={str(p.id):[{'id':u.id,'name':u.name,'symbol':u.symbol,'conversion':str(u.conversion_to_base)}
         for u in p.selling_units.all() if u.is_active] for p in products_qs}
    return render(request,'opening_stock.html',{'products':products_qs,'units_json':json.dumps(data),'branch':branch})


@admin_required
@require_POST
def opening_batch_delete(request, pk):
    batch=get_object_or_404(StockBatch,pk=pk,branch=current_branch(request),source='OPENING',purchase_item__isnull=True)
    if batch.legacy_novman_id is not None or batch.reference.startswith(('Transfer #','Adjustment:')):
        messages.error(request,'Imported/transfer opening stock cannot be deleted; use a stock adjustment.')
    elif batch.quantity_remaining_base!=batch.quantity_received_base or batch.sale_allocations.exists():
        messages.error(request,'Used batch cannot be deleted.')
    else:
        batch.delete();messages.success(request,'Unused opening batch deleted.')
    return redirect('stock')


@admin_required
def stock_adjust(request):
    branch=current_branch(request)
    if request.method=='POST':
        p=get_object_or_404(Product,pk=request.POST.get('product'))
        try:
            change=request.POST.get('adjustment_type')
            if change not in ('increase','decrease'): raise StockError('Invalid adjustment type.')
            ops.adjust(product=p,branch=branch,quantity=request.POST.get('quantity'),
                increase=change=='increase',unit_cost_base=request.POST.get('unit_cost','0'),
                user=request.user,reason=request.POST.get('reason','Stock adjustment'))
            messages.success(request,f'{branch.name} stock adjusted.')
            return redirect('stock')
        except StockError as exc: messages.error(request,str(exc))
    return render(request,'stock_adjust.html',{'products':Product.objects.filter(is_active=True),'branch':branch})


@admin_required
def purchases(request):
    branch=current_branch(request)
    qs=Purchase.objects.filter(branch=branch).select_related('created_by').prefetch_related('items__product','items__unit','items__batch')
    return render(request,'purchases.html',{'purchases':qs[:100],'branch':branch})


@admin_required
def purchase_create(request):
    branch=current_branch(request)
    products=Product.objects.filter(is_active=True).prefetch_related('selling_units')
    if request.method=='POST':
        p=get_object_or_404(Product,pk=request.POST.get('product'))
        unit=get_object_or_404(ProductUnit,pk=request.POST.get('unit'),product=p)
        try:
            ops.purchase_stock(product=p,branch=branch,unit=unit,
                quantity=request.POST.get('quantity'),cost_per_unit=request.POST.get('cost_per_unit'),
                supplier_name=request.POST.get('supplier_name',''),
                invoice_number=request.POST.get('invoice_number',''),notes=request.POST.get('notes',''),user=request.user)
            messages.success(request,f'Purchase added to {branch.name}.');return redirect('purchases')
        except StockError as exc:messages.error(request,str(exc))
    data={str(p.id):[{'id':u.id,'name':u.name,'symbol':u.symbol,'conversion':str(u.conversion_to_base)}
         for u in p.selling_units.all() if u.is_active] for p in products}
    return render(request,'purchase_form.html',{'products':products,'units_json':json.dumps(data),'branch':branch})


@admin_required
def purchase_edit(request, pk):
    p=get_object_or_404(Purchase.objects.prefetch_related('items__batch'),pk=pk,branch=current_branch(request))
    form=PurchaseMetaForm(request.POST or None,instance=p)
    if request.method=='POST' and form.is_valid():
        p=form.save()
        for item in p.items.select_related('batch'):
            if hasattr(item,'batch'):
                item.batch.reference=p.invoice_number or f'PUR-{p.pk:05d}'
                item.batch.save(update_fields=['reference'])
        messages.success(request,'Purchase details updated.');return redirect('purchases')
    return render(request,'purchase_edit.html',{'purchase':p,'form':form})


@admin_required
@require_POST
def purchase_delete(request, pk):
    p=get_object_or_404(Purchase.objects.prefetch_related('items__batch'),pk=pk,branch=current_branch(request))
    for item in p.items.all():
        batch=getattr(item,'batch',None)
        if batch and (batch.quantity_remaining_base!=batch.quantity_received_base or batch.sale_allocations.exists()):
            messages.error(request,'Purchase stock already used; cannot delete.');return redirect('purchases')
    p.delete();messages.success(request,'Unused purchase removed.');return redirect('purchases')


@login_required
def expenses(request):
    branch=current_branch(request)
    form=ExpenseForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False);obj.created_by=request.user;obj.branch=branch;obj.save()
        messages.success(request,'Expense saved.');return redirect('expenses')
    qs=Expense.objects.filter(branch=branch).select_related('created_by')
    return render(request,'expenses.html',{'form':form,'expenses':qs[:100],'branch':branch})


@login_required
def expense_edit(request, pk):
    e=get_object_or_404(Expense,pk=pk,branch=current_branch(request))
    form=ExpenseForm(request.POST or None,instance=e)
    if request.method=='POST' and form.is_valid():
        form.save();messages.success(request,'Expense updated.');return redirect('expenses')
    return render(request,'expense_form.html',{'form':form,'expense':e})


@login_required
@require_POST
def expense_delete(request,pk):
    e=get_object_or_404(Expense,pk=pk,branch=current_branch(request))
    e.delete();messages.success(request,'Expense deleted.');return redirect('expenses')


@admin_required
def reports(request):
    branch=current_branch(request)
    today=timezone.localdate()
    start=_date(request.GET.get('start'),today.replace(day=1));end=_date(request.GET.get('end'),today)
    sales=Sale.objects.filter(branch=branch,status='completed',sold_at__date__range=[start,end])
    spent=_money(Expense.objects.filter(branch=branch,expense_date__range=[start,end]),'amount')
    revenue=_money(sales,'grand_total');cost=_money(sales,'cogs_total')
    payments=CustomerPayment.objects.filter(branch=branch,is_voided=False,paid_at__date__range=[start,end])
    top=(SaleItem.objects.filter(sale__in=sales).values('product__name')
        .annotate(qty=Sum('base_quantity'),amount=Sum('final_total')).order_by('-amount')[:10])
    return render(request,'novman_reports.html',{'branch':branch,'start':start,'end':end,
        'sales_count':sales.count(),'revenue':revenue,'cost':cost,'gross':revenue-cost,
        'spent':spent,'net':revenue-cost-spent,'debt_collected':_money(payments,'amount'),
        'debt_sales':_money(sales.filter(payment_method='debt'),'grand_total'),
        'top_items':top})


@admin_required
@require_POST
@transaction.atomic
def customer_payment_create(request,pk):
    customer=get_object_or_404(Customer,pk=pk)
    try:
        payment=record_customer_payment(customer=customer,amount=request.POST.get('amount'),
            method=request.POST.get('method','cash'),reference=request.POST.get('reference',''),
            notes=request.POST.get('notes',''),user=request.user)
        payment.branch=current_branch(request)
        payment.save(update_fields=['branch'])
        messages.success(request,'Debt payment recorded at selected branch.')
    except StockError as exc:messages.error(request,str(exc))
    return redirect('customer_detail',pk=pk)


@admin_required
@require_POST
def customer_payment_void(request,pk):
    from .services import void_customer_payment
    payment=get_object_or_404(CustomerPayment,pk=pk,branch=current_branch(request))
    try:
        void_customer_payment(payment=payment,user=request.user,reason=request.POST.get('reason',''))
        messages.success(request,'Customer payment voided.')
    except StockError as exc:messages.error(request,str(exc))
    return redirect('customer_detail',pk=payment.customer_id)


@admin_required
def stock_transfers(request):
    branch=current_branch(request)
    if request.method=='POST':
        p=get_object_or_404(Product,pk=request.POST.get('product_id'))
        dest=get_object_or_404(Branch,pk=request.POST.get('destination_id'),is_active=True)
        try:
            ops.transfer(source=branch,destination=dest,product=p,
                quantity=request.POST.get('quantity'),user=request.user,
                notes=request.POST.get('notes',''))
            messages.success(request,f'Transferred {p.name} to {dest.name}.')
            return redirect('stock_transfers')
        except StockError as exc:messages.error(request,str(exc))
    transfers=StockTransfer.objects.filter(Q(source_branch=branch)|Q(destination_branch=branch))
    return render(request,'novman_transfers.html',{'branch':branch,
        'branches':Branch.objects.filter(is_active=True).exclude(pk=branch.pk),
        'products':Product.objects.filter(is_active=True).order_by('name','pk'),
        'transfers':transfers.select_related('source_branch','destination_branch','product','created_by')[:100]})


@login_required
def stocktakes(request):
    branch=current_branch(request)
    if request.method=='POST':
        try:
            s=ops.create_branch_stocktake(branch=branch,date=_date(request.POST.get('count_date'),timezone.localdate()),
                notes=request.POST.get('notes',''),user=request.user)
            messages.success(request,'Stocktake created.');return redirect('stocktake_detail',pk=s.pk)
        except StockError as exc:messages.error(request,str(exc))
    sessions=Stocktake.objects.filter(branch=branch).select_related('created_by','posted_by','cancelled_by')[:100]
    return render(request,'stocktakes.html',{'stocktakes':sessions,'today':timezone.localdate()})


@login_required
def stocktake_detail(request,pk):
    branch=current_branch(request)
    st=get_object_or_404(Stocktake,pk=pk,branch=branch)
    from .views import _save_stocktake_counts_from_post
    if request.method=='POST':
        action=request.POST.get('action','save')
        try:
            if action in ('save','post','refresh'):
                _save_stocktake_counts_from_post(st,request.POST)
                if action=='refresh': ops.refresh_branch_stocktake(stocktake=st)
                if action=='post': ops.post_branch_stocktake(stocktake=st,user=request.user)
            elif action=='cancel':
                if st.status!='draft': raise StockError('Only draft can be cancelled.')
                st.status='cancelled';st.cancelled_by=request.user;st.cancelled_at=timezone.now()
                st.save(update_fields=['status','cancelled_by','cancelled_at'])
            else:raise StockError('Invalid stocktake action.')
            messages.success(request,'Stocktake updated.');return redirect('stocktake_detail',pk=pk)
        except StockError as exc:messages.error(request,str(exc))
    lines=list(st.lines.select_related('product','count_unit','adjustment')
        .prefetch_related('product__selling_units').order_by('product__name','product_id'))
    for line in lines:
        line.unit_options=[{'value':'base','label':f'Base ({line.product.base_unit})','conversion':Decimal('1')},
            *[{'value':str(u.pk),'label':f'{u.name} ({u.symbol})','conversion':u.conversion_to_base}
             for u in line.product.selling_units.all() if u.is_active and not (u.conversion_to_base==1 and u.symbol.upper()==line.product.base_unit.upper())]]
        line.selected_unit_value=str(line.count_unit_id) if line.count_unit_id else 'base'
    return render(request,'stocktake_detail.html',{'stocktake':st,'lines':lines,
        'counted_lines':sum(x.counted_qty_base is not None for x in lines),
        'variance_lines':sum(x.counted_qty_base is not None and x.difference_base!=0 for x in lines)})


@login_required
def end_day(request):
    """Branch-level end-day snapshot (no accidental company-wide reconciliation)."""
    branch=current_branch(request)
    day=_date(request.GET.get('date'),timezone.localdate())
    sales=Sale.objects.filter(branch=branch,status='completed',sold_at__date=day)
    cash=_money(sales.filter(payment_method='cash'),'grand_total')
    mobile=_money(sales.filter(payment_method='mobile_money'),'grand_total')
    bank=_money(sales.filter(payment_method='bank'),'grand_total')
    debt=_money(sales.filter(payment_method='debt'),'grand_total')
    payments=CustomerPayment.objects.filter(branch=branch,paid_at__date=day,is_voided=False)
    collected_cash=_money(payments.filter(method='cash'),'amount')
    collected_mobile=_money(payments.filter(method='mobile_money'),'amount')
    collected_bank=_money(payments.filter(method='bank'),'amount')
    return render(request,'novman_end_day.html',{'branch':branch,'day':day,
        'sales_count':sales.count(),'cash':cash,'mobile':mobile,'bank':bank,'debt':debt,
        'collected_cash':collected_cash,'collected_mobile':collected_mobile,
        'collected_bank':collected_bank,'expected_cash_receipts':cash+collected_cash})


@login_required
def blocked_export(request,*args,**kwargs):
    return HttpResponseForbidden('Legacy export/closing is disabled in Novman V2 until branch-safe export/reconciliation is verified. Use database backups.')
