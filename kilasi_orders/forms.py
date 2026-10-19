from django import forms
from core.models import Product
from .models import CustomerOrder, OrderPayment

class CleanFields:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget,forms.CheckboxInput):f.widget.attrs.setdefault('class','form-control')

class OrderForm(CleanFields,forms.ModelForm):
    class Meta:
        model=CustomerOrder
        fields=['customer_name','customer_phone','product','custom_product_name','description','quantity','total_amount','expected_date','notes']
        widgets={'expected_date':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':3}),
                 'notes':forms.Textarea(attrs={'rows':2}),'total_amount':forms.NumberInput(attrs={'min':0,'step':'0.01'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['product'].queryset=Product.objects.filter(is_active=True).order_by('name')
        self.fields['product'].required=False
    def clean(self):
        d=super().clean()
        if not d.get('product') and not (d.get('custom_product_name') or '').strip():
            self.add_error('custom_product_name','Select a product or enter a custom product name.')
        return d

class OrderPaymentForm(CleanFields,forms.ModelForm):
    class Meta:
        model=OrderPayment
        fields=['amount','method','reference','notes']
        widgets={'amount':forms.NumberInput(attrs={'min':'0.01','step':'0.01'})}

class ReturnForm(CleanFields,forms.Form):
    sale_item = forms.IntegerField(min_value=1,label='Item from this receipt')
    quantity = forms.DecimalField(max_digits=14,decimal_places=3,min_value=0.001,widget=forms.NumberInput(attrs={'step':'0.001','min':'0.001'}))
    reason = forms.CharField(max_length=240,widget=forms.Textarea(attrs={'rows':2}),min_length=5)
    restock = forms.BooleanField(required=False,initial=True,label='Restock resaleable product')
    refund_method = forms.ChoiceField(choices=OrderPayment.METHODS)
    refund_reference = forms.CharField(max_length=100,required=False)
