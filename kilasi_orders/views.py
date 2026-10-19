"""Retail order/return views using the existing shared Kilasi base layout."""
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from core.models import Customer, Sale, SaleItem
from core.novman_branch import current_branch, is_admin
from .forms import OrderForm, OrderPaymentForm, ReturnForm
from .models import CustomerOrder, OrderPayment, SalesReturn
from .services import take_order_payment, void_order_payment, change_order_status, post_sale_return

ZERO=Decimal('0.00')
def branch_for_retail(request):
    # Also guarded by Phase 4B TransportRouteFirewall; no transport sessions.
    if getattr(request,'transport_access',None):
        raise PermissionDenied('HAMA NA KILASI users may not access Retail.')
    if not (is_admin(request.user) or getattr(getattr(request.user,'profile',None),'role','')=='cashier'):
        raise PermissionDenied('Retail account required.')
    return current_branch(request)

def admin_branch(request):
    branch=branch_for_retail(request)
    if not is_admin(request.user):raise PermissionDenied('Administrator approval required.')
    return branch

@login_required
def orders(request):
    branch=branch_for_retail(request)
    q=request.GET.get('q','').strip()[:110]
    status=request.GET.get('status','').strip()
    qs=CustomerOrder.objects.filter(branch=branch).select_related('product','customer','created_by')
    if q:qs=qs.filter(Q(customer_name__icontains=q)|Q(customer_phone__icontains=q)|Q(custom_product_name__icontains=q)|Q(description__icontains=q))
    if status in dict(CustomerOrder.STATUS):qs=qs.filter(order_status=status)
    allorders=CustomerOrder.objects.filter(branch=branch)
    totals=allorders.exclude(order_status='cancelled').aggregate(v=Sum('total_amount'))['v'] or ZERO
    active_ids=allorders.exclude(order_status='cancelled').values_list('pk',flat=True)
    deposits=OrderPayment.objects.filter(order_id__in=active_ids,is_voided=False).aggregate(v=Sum('amount'))['v'] or ZERO
    return render(request,'kilasi_orders/orders.html',{
        'branch':branch,'rows':qs[:200],'q':q,'status':status,'status_choices':CustomerOrder.STATUS,'total_orders':allorders.count(),
        'active_value':totals,'deposits':deposits,'unpaid':max(ZERO,totals-deposits),
    })

@login_required
def order_new(request):
    branch=branch_for_retail(request)
    form=OrderForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        obj=form.save(commit=False)
        obj.branch=branch;obj.created_by=request.user
        phone=(obj.customer_phone or '').strip()
        if phone:obj.customer=Customer.objects.filter(phone=phone,is_active=True).first()
        obj.save()
        messages.success(request,f'Order {obj.order_no} created. Stock is NOT reserved or sold automatically.')
        return redirect('kilasi_orders:order_detail',pk=obj.pk)
    return render(request,'kilasi_orders/order_form.html',{'form':form,'branch':branch})

@login_required
def order_detail(request,pk):
    branch=branch_for_retail(request)
    order=get_object_or_404(CustomerOrder.objects.select_related('product','customer','created_by').prefetch_related('payments__received_by'),pk=pk,branch=branch)
    return render(request,'kilasi_orders/order_detail.html',{'branch':branch,'order':order,'payment_form':OrderPaymentForm()})

@login_required
@require_POST
def order_pay(request,pk):
    branch=branch_for_retail(request)
    form=OrderPaymentForm(request.POST)
    if form.is_valid():
        try:
            d=form.cleaned_data
            take_order_payment(order_id=pk,branch=branch,user=request.user,amount=d['amount'],method=d['method'],reference=d['reference'],notes=d['notes'])
            messages.success(request,'Order deposit recorded separately from POS revenue.')
        except (ValidationError,CustomerOrder.DoesNotExist) as e:messages.error(request,str(e))
    else:messages.error(request,form.errors.as_text())
    return redirect('kilasi_orders:order_detail',pk=pk)

@login_required
@require_POST
def order_payment_void(request,pk):
    branch=admin_branch(request)
    payment=get_object_or_404(OrderPayment,pk=pk,order__branch=branch)
    try:
        void_order_payment(pk=pk,branch=branch,user=request.user,reason=request.POST.get('reason',''))
        messages.success(request,'Order payment voided; audit history preserved.')
    except ValidationError as e:messages.error(request,str(e))
    return redirect('kilasi_orders:order_detail',pk=payment.order_id)

@login_required
@require_POST
def order_status(request,pk):
    branch=admin_branch(request)
    try:
        change_order_status(order_id=pk,branch=branch,status=request.POST.get('status',''))
        messages.success(request,'Order status updated. No stock or revenue was posted.')
    except (CustomerOrder.DoesNotExist,ValidationError) as e:messages.error(request,str(e))
    return redirect('kilasi_orders:order_detail',pk=pk)

@login_required
def returns(request):
    branch=admin_branch(request)
    q=request.GET.get('q','').strip()[:110]
    qs=SalesReturn.objects.filter(branch=branch).select_related('sale','created_by')
    if q:qs=qs.filter(Q(sale__receipt_no__icontains=q)|Q(reason__icontains=q))
    totals=SalesReturn.objects.filter(branch=branch).aggregate(refund=Sum('total_refund'),restored=Sum('stock_cost_restored'))
    return render(request,'kilasi_orders/returns.html',{'branch':branch,'rows':qs[:200], 'q':q,
          'refund_total':totals['refund'] or ZERO,'restocked_cost':totals['restored'] or ZERO})

@login_required
def return_new(request):
    branch=admin_branch(request)
    sale_id=request.POST.get('sale') if request.method=='POST' else request.GET.get('sale')
    sale=None
    if sale_id and str(sale_id).isdigit():
        sale=get_object_or_404(Sale.objects.prefetch_related('items__product','items__unit'),pk=int(sale_id),branch=branch)
    form=ReturnForm(request.POST or None)
    if request.method=='POST':
        if sale is None:form.add_error(None,'Choose a receipt first.')
        elif form.is_valid():
            d=form.cleaned_data
            try:
                ret=post_sale_return(branch=branch,sale_id=sale.pk,sale_item_id=d['sale_item'],quantity=d['quantity'],
                    reason=d['reason'],restock=d['restock'],method=d['refund_method'],reference=d['refund_reference'],user=request.user)
                messages.success(request,f'{ret.return_no} posted. Refund is a manual payment record; no external bank transfer occurred.')
                return redirect('kilasi_orders:return_detail',pk=ret.pk)
            except (ValidationError,Sale.DoesNotExist,SaleItem.DoesNotExist,ValueError) as e:
                form.add_error(None,str(e))
    sales=Sale.objects.filter(branch=branch,status='completed').exclude(payment_method='debt').order_by('-sold_at')[:200]
    return render(request,'kilasi_orders/return_form.html',{'branch':branch,'sale':sale,'sales':sales,'form':form})

@login_required
def return_detail(request,pk):
    branch=admin_branch(request)
    ret=get_object_or_404(SalesReturn.objects.select_related('sale','created_by')
        .prefetch_related('lines__sale_item__product','lines__restored_allocations'),pk=pk,branch=branch)
    return render(request,'kilasi_orders/return_detail.html',{'branch':branch,'ret':ret})
