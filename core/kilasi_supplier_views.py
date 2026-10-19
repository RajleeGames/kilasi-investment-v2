"""Native, permission-checked supplier administration for KILASI Investment V2."""
from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.decorators import admin_required
from core.models import Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ('name', 'contact')
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 255, 'placeholder': 'Supplier or company name'}),
            'contact': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 100, 'placeholder': 'Phone or contact details'}),
        }

    def clean_name(self):
        name = ' '.join((self.cleaned_data.get('name') or '').split())
        if not name:
            raise forms.ValidationError('Enter the supplier name.')
        duplicates = Supplier.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError('A supplier with this name already exists.')
        return name


@admin_required
@require_http_methods(['GET'])
def suppliers(request):
    q = (request.GET.get('q') or '').strip()[:100]
    records = Supplier.objects.annotate(products_count=Count('products')).order_by('name', 'id')
    if q:
        records = records.filter(Q(name__icontains=q) | Q(contact__icontains=q))
    page = Paginator(records, 30).get_page(request.GET.get('page'))
    return render(request, 'kilasi_suppliers.html', {'page': page, 'q': q, 'total': records.count()})


@admin_required
@require_http_methods(['GET', 'POST'])
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        supplier = form.save()
        messages.success(request, f'Supplier {supplier.name} added.')
        return redirect('kilasi_suppliers')
    return render(request, 'kilasi_supplier_form.html', {'form': form, 'heading': 'Add supplier', 'submit_label': 'Save supplier'})


@admin_required
@require_http_methods(['GET', 'POST'])
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Supplier details updated. Linked product history was preserved.')
        return redirect('kilasi_suppliers')
    return render(request, 'kilasi_supplier_form.html', {'form': form, 'heading': 'Edit supplier', 'submit_label': 'Save changes', 'supplier': supplier})
