"""Independent document UI using Novman's ledger, authentication and branches.

Never changes Sale, SaleItem, StockBatch or FIFO allocations.
"""
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import admin_required
from core.models import CustomerPayment, Sale
from core.novman_branch import current_branch
from core.services import StockError, record_customer_payment
from .forms import DeliveryForm, InvoiceForm, InvoicePaymentForm, ProformaForm, ProformaLineSet
from .models import DeliveryNote, Invoice, InvoiceAllocation, Proforma, ZERO, cash


def _sale(branch, sale_id):
    return get_object_or_404(
        Sale.objects.select_related('customer', 'cashier').prefetch_related('items__product', 'items__unit'),
        pk=sale_id, branch=branch,
    )


def _ctx(request, **kwargs):
    return {'branch': current_branch(request), **kwargs}


@admin_required
def home(request):
    branch = current_branch(request)
    recent_sales = Sale.objects.filter(branch=branch, status='completed').order_by('-sold_at', '-pk')[:12]
    return render(request, 'kilasi_billing/home.html', _ctx(
        request, invoice_count=Invoice.objects.filter(sale__branch=branch).count(),
        proforma_count=Proforma.objects.filter(branch=branch).count(),
        delivery_count=DeliveryNote.objects.filter(sale__branch=branch).count(),
        recent_sales=recent_sales,
    ))


@admin_required
def proforma_list(request):
    branch = current_branch(request)
    qs = Proforma.objects.filter(branch=branch).prefetch_related('lines')
    search = (request.GET.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(customer_name__icontains=search))
    return render(request, 'kilasi_billing/proforma_list.html', _ctx(request, rows=qs[:150], q=search))


@admin_required
def proforma_edit(request, pk=None):
    branch = current_branch(request)
    obj = get_object_or_404(Proforma, pk=pk, branch=branch) if pk else Proforma(branch=branch, created_by=request.user)
    if pk and obj.status != 'draft':
        messages.error(request, 'Only a draft proforma can be edited.')
        return redirect('billing:proforma_detail', pk=obj.pk)
    form = ProformaForm(request.POST or None, instance=obj)
    lines = ProformaLineSet(request.POST or None, instance=obj, prefix='lines')
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            doc = form.save(commit=False)
            doc.branch = branch
            if not doc.pk:
                doc.created_by = request.user
            doc.save()
            lines.instance = doc
            lines.save()
        messages.success(request, f'{doc.number} saved.')
        return redirect('billing:proforma_detail', pk=doc.pk)
    return render(request, 'kilasi_billing/proforma_form.html', _ctx(
        request, form=form, lines=lines, doc=obj if pk else None,
    ))


@admin_required
def proforma_detail(request, pk):
    doc = get_object_or_404(Proforma.objects.prefetch_related('lines'), pk=pk, branch=current_branch(request))
    return render(request, 'kilasi_billing/proforma_detail.html', _ctx(request, doc=doc))


@admin_required
@require_POST
def proforma_status(request, pk):
    with transaction.atomic():
        doc = get_object_or_404(Proforma.objects.select_for_update(), pk=pk, branch=current_branch(request))
        action = (request.POST.get('action') or '').strip()
        if action == 'issue' and doc.status == 'draft' and doc.lines.exists():
            doc.status = 'issued'
        elif action == 'cancel' and doc.status in ('draft', 'issued'):
            doc.status = 'cancelled'
        else:
            messages.error(request, 'Invalid proforma status change.')
            return redirect('billing:proforma_detail', pk=pk)
        doc.save(update_fields=['status'])
    messages.success(request, f'Proforma is now {doc.get_status_display().lower()}.')
    return redirect('billing:proforma_detail', pk=pk)


@admin_required
def invoice_list(request):
    branch = current_branch(request)
    qs = Invoice.objects.filter(sale__branch=branch).select_related('sale__customer').prefetch_related('allocations__payment')
    search = (request.GET.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(sale__receipt_no__icontains=search)
                       | Q(sale__customer_name__icontains=search))
    return render(request, 'kilasi_billing/invoice_list.html', _ctx(request, rows=qs[:150], q=search))


@admin_required
def invoice_new(request, sale_id):
    branch = current_branch(request)
    sale = _sale(branch, sale_id)
    existing = Invoice.objects.filter(sale=sale).first()
    if existing:
        return redirect('billing:invoice_detail', pk=existing.pk)
    if sale.status != 'completed':
        messages.error(request, 'Cannot issue an invoice for a voided sale.')
        return redirect('sales')
    form = InvoiceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            doc = form.save(commit=False)
            doc.sale = sale
            doc.created_by = request.user
            doc.save()
        messages.success(request, f'{doc.number} issued for {sale.receipt_no}.')
        return redirect('billing:invoice_detail', pk=doc.pk)
    return render(request, 'kilasi_billing/invoice_new.html', _ctx(request, form=form, sale=sale))


@admin_required
def invoice_detail(request, pk):
    doc = get_object_or_404(
        Invoice.objects.select_related('sale__customer', 'sale__cashier')
        .prefetch_related('sale__items__product', 'sale__items__unit', 'allocations__payment'),
        pk=pk, sale__branch=current_branch(request),
    )
    customer_payments_unallocated = False
    if doc.sale.payment_method == 'debt' and doc.sale.customer_id:
        customer_payments_unallocated = CustomerPayment.objects.filter(
            branch=doc.sale.branch, customer=doc.sale.customer, is_voided=False,
            kilasi_invoice_allocation__isnull=True,
        ).exists()
    return render(request, 'kilasi_billing/invoice_detail.html', _ctx(
        request, doc=doc, has_unallocated=customer_payments_unallocated,
    ))


@admin_required
@require_POST
def invoice_pay(request, pk):
    form = InvoicePaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid payment: check amount, method and reference.')
        return redirect('billing:invoice_detail', pk=pk)
    try:
        with transaction.atomic():
            branch = current_branch(request)
            invoice = get_object_or_404(
                Invoice.objects.select_for_update().select_related('sale__customer'),
                pk=pk, sale__branch=branch,
            )
            sale = invoice.sale
            if sale.status != 'completed' or sale.payment_method != 'debt' or not sale.customer_id:
                raise StockError('Only active debt invoices accept partial payments.')
            amount = cash(form.cleaned_data['amount'])
            if amount > invoice.balance:
                raise StockError('Payment exceeds this invoice\'s unallocated balance.')
            # Branch balance is independent of a customer's other branch debts.
            branch_debt = Sale.objects.filter(
                branch=branch, customer=sale.customer, status='completed', payment_method='debt',
            ).aggregate(v=Sum('grand_total'))['v'] or ZERO
            branch_collected = CustomerPayment.objects.filter(
                branch=branch, customer=sale.customer, is_voided=False,
            ).aggregate(v=Sum('amount'))['v'] or ZERO
            if amount > max(ZERO, cash(branch_debt - branch_collected)):
                raise StockError('Payment exceeds remaining customer debt at this branch. Reconcile earlier payments first.')
            payment = record_customer_payment(
                customer=sale.customer, amount=amount,
                method=form.cleaned_data['method'],
                reference=form.cleaned_data['reference'],
                notes=form.cleaned_data['notes'], user=request.user,
            )
            payment.branch = branch
            payment.save(update_fields=['branch'])
            InvoiceAllocation.objects.create(invoice=invoice, payment=payment)
        messages.success(request, 'Payment recorded once in the Novman customer ledger and linked to the invoice.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('billing:invoice_detail', pk=pk)


@admin_required
def delivery_list(request):
    branch = current_branch(request)
    rows = DeliveryNote.objects.filter(sale__branch=branch).select_related('sale__customer')[:150]
    return render(request, 'kilasi_billing/delivery_list.html', _ctx(request, rows=rows))


@admin_required
def delivery_new(request, sale_id):
    sale = _sale(current_branch(request), sale_id)
    existing = DeliveryNote.objects.filter(sale=sale).first()
    if existing:
        return redirect('billing:delivery_detail', pk=existing.pk)
    if sale.status != 'completed':
        messages.error(request, 'Cannot issue a delivery note for a voided sale.')
        return redirect('sales')
    initial = {'delivered_to': sale.customer.name if sale.customer_id else sale.customer_name}
    form = DeliveryForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            doc = form.save(commit=False)
            doc.sale = sale
            doc.created_by = request.user
            doc.save()
        messages.success(request, f'{doc.number} issued. Stock was not deducted again.')
        return redirect('billing:delivery_detail', pk=doc.pk)
    return render(request, 'kilasi_billing/delivery_new.html', _ctx(request, form=form, sale=sale))


@admin_required
def delivery_detail(request, pk):
    doc = get_object_or_404(
        DeliveryNote.objects.select_related('sale').prefetch_related('sale__items__product', 'sale__items__unit'),
        pk=pk, sale__branch=current_branch(request),
    )
    return render(request, 'kilasi_billing/delivery_detail.html', _ctx(request, doc=doc))
