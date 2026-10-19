"""Transaction boundaries for order deposits and FIFO-safe customer sale returns."""
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from core.models import Sale, SaleItem, StockBatch
from .models import CustomerOrder, OrderPayment, SalesReturn, SalesReturnLine, ReturnStockAllocation

ZERO=Decimal('0.00')
def cash(value):return Decimal(str(value)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)

def _lock_order(pk,branch):
    return CustomerOrder.objects.select_for_update().get(pk=pk,branch=branch)

@transaction.atomic
def take_order_payment(*,order_id,branch,amount,method,reference='',notes='',user):
    order=_lock_order(order_id,branch)
    if order.order_status=='cancelled':raise ValidationError('Cannot take payment for a cancelled order.')
    amount=cash(amount)
    if amount<=ZERO:raise ValidationError('Payment must be greater than zero.')
    if method not in dict(OrderPayment.METHODS):raise ValidationError('Invalid payment method.')
    paid=OrderPayment.objects.filter(order=order,is_voided=False).aggregate(v=Sum('amount'))['v'] or ZERO
    if amount>order.total_amount-paid:raise ValidationError('Payment exceeds remaining order balance.')
    return OrderPayment.objects.create(order=order,amount=amount,method=method,reference=reference[:100],notes=notes[:240],received_by=user)

@transaction.atomic
def void_order_payment(*,pk,branch,user,reason):
    payment=OrderPayment.objects.select_related('order').get(pk=pk,order__branch=branch)
    _lock_order(payment.order_id,branch)
    payment=OrderPayment.objects.select_for_update().get(pk=pk)
    if payment.is_voided:raise ValidationError('Payment already voided.')
    if len(reason.strip())<5:raise ValidationError('Please enter a correction reason (at least 5 characters).')
    payment.is_voided=True;payment.voided_at=timezone.now();payment.voided_by=user;payment.void_reason=reason.strip()[:240]
    payment.save(update_fields=['is_voided','voided_at','voided_by','void_reason'])
    return payment

@transaction.atomic
def change_order_status(*,order_id,branch,status):
    order=_lock_order(order_id,branch)
    allowed={'pending':{'in_progress','cancelled'},'in_progress':{'completed','cancelled'},'completed':set(),'cancelled':set()}
    if status not in allowed.get(order.order_status,set()):raise ValidationError('Invalid or final order status transition.')
    if status=='cancelled' and OrderPayment.objects.filter(order=order,is_voided=False).exists():
        raise ValidationError('Void/refund all order deposits before cancellation.')
    order.order_status=status
    if status=='completed':order.completed_at=timezone.now()
    order.save(update_fields=['order_status','completed_at','updated_at'])
    return order

@transaction.atomic
def post_sale_return(*,branch,sale_id,sale_item_id,quantity,reason,restock,method,reference,user):
    """Posted returns are immutable. No external money transfer is performed.

    Debt sales are deliberately blocked: applying credit requires an integrated
    debt-ledger adjustment rather than quietly leaving Customer.current_balance wrong.
    """
    sale=Sale.objects.select_for_update().get(pk=sale_id,branch=branch)
    if sale.status!='completed' or sale.payment_method=='debt':
        raise ValidationError('Only completed, non-debt POS sales can be returned in Phase 6B.')
    if method not in dict(OrderPayment.METHODS):raise ValidationError('Select a valid refund method.')
    reason=(reason or '').strip()
    if len(reason)<5:raise ValidationError('Enter a return reason of at least 5 characters.')
    item=SaleItem.objects.select_for_update().select_related('product').get(pk=sale_item_id,sale=sale)
    qty=Decimal(str(quantity))
    if qty<=0 or qty.as_tuple().exponent < -3:raise ValidationError('Enter a positive quantity with at most 3 decimal places.')
    returned=SalesReturnLine.objects.filter(sale_item=item).aggregate(v=Sum('quantity'))['v'] or Decimal('0')
    if qty>item.quantity-returned:raise ValidationError('Return exceeds unreturned quantity on the receipt.')
    if not item.quantity or not item.base_quantity:raise ValidationError('Original item has invalid quantity.')
    base_qty=qty*item.conversion_to_base
    if base_qty != base_qty.quantize(Decimal('0.001')):raise ValidationError('Quantity conversion is too precise for stock units.')
    net_before=sale.subtotal-sale.item_discount_total
    if net_before<=0:raise ValidationError('Sale has no refundable net value.')
    item_net=Decimal(item.final_total)*(Decimal(sale.grand_total)/net_before)
    used_refunds=SalesReturnLine.objects.filter(sale_item=item).aggregate(v=Sum('refund_amount'))['v'] or ZERO
    full_entitlement=cash(item_net)
    refund=min(cash(item_net*qty/item.quantity),full_entitlement-used_refunds)
    if refund<0:raise ValidationError('Refund entitlement exhausted.')
    ret=SalesReturn.objects.create(branch=branch,sale=sale,reason=reason,refund_method=method,refund_reference=(reference or '')[:100],total_refund=refund,created_by=user)
    line=SalesReturnLine.objects.create(sale_return=ret,sale_item=item,quantity=qty,base_quantity=base_qty,refund_amount=refund,restocked=bool(restock))
    cost=ZERO
    allocations=list(item.allocations.select_related('batch').order_by('pk'))
    remaining=base_qty
    for original in allocations:
        if remaining<=0:break
        # Tracks ALL prior returned items (including damaged/no-restock) so
        # a later return cannot reuse the same original allocation.
        previously_returned=ReturnStockAllocation.objects.filter(original_allocation=original).aggregate(v=Sum('quantity_base'))['v'] or Decimal('0')
        available=original.quantity_base-previously_returned
        take=min(remaining,available)
        if take<=0:continue
        new_batch=None;batch_cost=ZERO
        if restock:
            unit_cost=original.unit_cost_base
            batch_cost=cash(take*unit_cost)
            new_batch=StockBatch.objects.create(product=item.product,branch=branch,source='OPENING',
                reference=f'Customer return {ret.return_no}, source batch {original.batch_id}'[:100],
                quantity_received_base=take,quantity_remaining_base=take,unit_cost_base=unit_cost,
                received_at=timezone.now(),created_by=user)
        ReturnStockAllocation.objects.create(return_line=line,original_allocation=original,
            new_stock_batch=new_batch,quantity_base=take,cost_total=batch_cost)
        cost+=batch_cost;remaining-=take
    if remaining>0:raise ValidationError('Cannot reconcile original FIFO sale allocations; no return was posted.')
    line.cost_restored=cost;line.save(update_fields=['cost_restored'])
    ret.stock_cost_restored=cost;ret.save(update_fields=['stock_cost_restored'])
    return ret
