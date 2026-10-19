from django import forms
from django.core.exceptions import ValidationError
from core.models import Product
from .models import GenjiSale, UsedTopupStock, CATEGORY


class CleanForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-control')


class GenjiForm(forms.ModelForm):
    class Meta:
        model = GenjiSale
        fields = ['customer_name','customer_phone','item_name','buy_date','sell_date',
                  'buying_cost','selling_price','quantity','notes']
        widgets = {'buy_date':forms.DateInput(attrs={'type':'date'}),
                   'sell_date':forms.DateInput(attrs={'type':'date'}),
                   'buying_cost':forms.NumberInput(attrs={'step':'0.01','min':'0'}),
                   'selling_price':forms.NumberInput(attrs={'step':'0.01','min':'0'}),
                   'notes':forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values(): field.widget.attrs.setdefault('class','form-control')
    def clean(self):
        d=super().clean()
        if d.get('buy_date') and d.get('sell_date') and d['sell_date'] < d['buy_date']:
            self.add_error('sell_date', 'Sell date cannot precede buy date.')
        return d


class TopupForm(CleanForm):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True,base_unit='PCS').order_by('name'))
    quantity = forms.DecimalField(min_value=1, max_digits=14, decimal_places=3, initial=1)
    used_item_name = forms.CharField(max_length=255)
    used_item_category = forms.ChoiceField(choices=CATEGORY)
    used_item_condition = forms.CharField(required=False,max_length=255)
    used_item_buying_price = forms.DecimalField(min_value=0, max_digits=14,decimal_places=2)
    addon_amount = forms.DecimalField(min_value=0,max_digits=14,decimal_places=2)
    customer_name = forms.CharField(required=False,max_length=150)
    customer_phone = forms.CharField(required=False,max_length=50)
    note = forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3}))
    def clean_quantity(self):
        q=self.cleaned_data['quantity']
        if q != q.to_integral_value():raise ValidationError('Only whole pieces are supported.')
        return q


class UsedSaleForm(CleanForm):
    sold_price = forms.DecimalField(min_value=0,max_digits=14,decimal_places=2)
    customer_name = forms.CharField(required=False,max_length=150)
    customer_phone = forms.CharField(required=False,max_length=50)
    note = forms.CharField(required=False,widget=forms.Textarea(attrs={'rows':3}))

class UsedStockEditForm(forms.ModelForm):
    """Asking price/description only; financial accepted cost stays immutable."""
    class Meta:
        model = UsedTopupStock
        fields = ['item_name','item_category','condition','asking_price','note']
        widgets = {'asking_price': forms.NumberInput(attrs={'min':'0','step':'0.01'}),
                   'note': forms.Textarea(attrs={'rows':3})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')
