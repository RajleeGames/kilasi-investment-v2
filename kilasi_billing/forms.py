from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory

from .models import Proforma, ProformaLine, Invoice, DeliveryNote


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-control')


class ProformaForm(StyledForm):
    class Meta:
        model = Proforma
        fields = ['customer_name', 'customer_phone', 'customer_address', 'issue_date', 'valid_until', 'notes']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('valid_until') and cleaned.get('issue_date') and cleaned['valid_until'] < cleaned['issue_date']:
            self.add_error('valid_until', 'Expiry date cannot be earlier than issue date.')
        return cleaned


class ProformaLineForm(StyledForm):
    class Meta:
        model = ProformaLine
        fields = ['description', 'quantity', 'unit', 'unit_price', 'discount']
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.001', 'min': '0.001'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'discount': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }


ProformaLineSet = inlineformset_factory(
    Proforma, ProformaLine, form=ProformaLineForm,
    extra=3, can_delete=True, min_num=1, validate_min=True,
)


class InvoiceForm(StyledForm):
    class Meta:
        model = Invoice
        fields = ['issue_date', 'due_date', 'notes']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'due_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('due_date') and cleaned.get('issue_date') and cleaned['due_date'] < cleaned['issue_date']:
            self.add_error('due_date', 'Due date cannot be earlier than invoice date.')
        return cleaned


class DeliveryForm(StyledForm):
    class Meta:
        model = DeliveryNote
        fields = ['delivered_to', 'delivery_address', 'vehicle_or_driver', 'issue_date', 'notes']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class InvoicePaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=14, decimal_places=2,
                                widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}))
    method = forms.ChoiceField(choices=[('cash','Cash'),('mobile_money','Mobile Money'),('bank','Bank')],
                               widget=forms.Select(attrs={'class': 'form-control'}))
    reference = forms.CharField(required=False, max_length=100, widget=forms.TextInput(attrs={'class': 'form-control'}))
    notes = forms.CharField(required=False, max_length=180, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
