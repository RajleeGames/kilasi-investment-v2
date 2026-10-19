from django import forms
from django.contrib.auth.models import User
from .models import Category, Product, ProductUnit, Expense, ShopSettings, Purchase, Customer


class CleanModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'toggle-input')
            elif isinstance(widget, forms.FileInput):
                widget.attrs.setdefault('class', 'file-input')
            else:
                widget.attrs.setdefault('class', 'form-control')


class ProductForm(CleanModelForm):
    class Meta:
        model = Product
        fields = ['name', 'retail_division', 'category', 'supplier', 'barcode', 'base_unit', 'low_stock_level', 'is_active']
        widgets = {
            'low_stock_level': forms.NumberInput(attrs={'step': 'any', 'inputmode': 'decimal'}),
        }


class ProductUnitForm(forms.ModelForm):
    class Meta:
        model = ProductUnit
        fields = [
            'name',
            'symbol',
            'conversion_to_base',
            'selling_price',
            'is_default',
            'is_active',
        ]

        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Kilogram, 500 Gram, Carton',
                'autocomplete': 'off',
            }),

            'symbol': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Kg, g, Carton',
                'autocomplete': 'off',
            }),

            'conversion_to_base': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.001',
                'min': '0.001',
                'placeholder': 'e.g. 1',
            }),

            'selling_price': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': 'e.g. 25000',
            }),
        }

        labels = {
            'name': 'Selling unit name',
            'symbol': 'Symbol',
            'conversion_to_base': 'Conversion to base unit',
            'selling_price': 'Selling price',
            'is_default': 'Default selling unit',
            'is_active': 'Active',
        }

        help_texts = {
            'name': (
                'The same product cannot have two selling units with the same name.'
            ),

            'symbol': (
                'Short name shown in POS and receipts. Example: Kg, g, or Carton.'
            ),

            'conversion_to_base': (
                'How much base stock one selling unit uses. '
                'Example: if the product base unit is KG, Kilogram = 1 '
                'and Half KG = 0.5.'
            ),

            'selling_price': (
                'Selling price for one of this unit.'
            ),
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Product is passed from product_edit.
        # When editing an existing unit, fall back to instance.product.
        self.product = product

        if not self.product and self.instance and self.instance.pk:
            self.product = self.instance.product

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()

        if not name:
            raise forms.ValidationError(
                'Enter a selling unit name.'
            )

        if self.product:
            duplicates = ProductUnit.objects.filter(
                product=self.product,
                name__iexact=name,
            )

            # When editing an existing unit, don't compare it with itself.
            if self.instance and self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)

            if duplicates.exists():
                raise forms.ValidationError(
                    f'"{name}" already exists for this product. '
                    f'Edit the existing {name} unit instead, '
                    f'or enter a different unit name.'
                )

        return name

    def clean_symbol(self):
        symbol = (self.cleaned_data.get('symbol') or '').strip()

        if not symbol:
            raise forms.ValidationError(
                'Enter a short symbol, for example Kg, g, Pc, Litre or Carton.'
            )

        return symbol

    def clean_conversion_to_base(self):
        conversion = self.cleaned_data.get('conversion_to_base')

        if conversion is None:
            raise forms.ValidationError(
                'Enter how much base stock this selling unit represents.'
            )

        if conversion <= 0:
            raise forms.ValidationError(
                'Conversion must be greater than zero. '
                'Example: 1 Kilogram = 1 if the product base unit is KG.'
            )

        return conversion

    def clean_selling_price(self):
        price = self.cleaned_data.get('selling_price')

        if price is None:
            raise forms.ValidationError(
                'Enter the selling price.'
            )

        if price < 0:
            raise forms.ValidationError(
                'Selling price cannot be negative.'
            )

        return price

    def clean(self):
        cleaned_data = super().clean()

        is_default = cleaned_data.get('is_default')
        is_active = cleaned_data.get('is_active')

        if is_default and not is_active:
            self.add_error(
                'is_active',
                'A default selling unit must also be active.'
            )

        return cleaned_data


class CategoryForm(CleanModelForm):
    class Meta:
        model = Category
        fields = ['name', 'is_active']


class ExpenseForm(CleanModelForm):
    class Meta:
        model = Expense
        fields = ['category', 'description', 'amount', 'expense_date']
        widgets = {
            'amount': forms.NumberInput(attrs={'step': 'any', 'inputmode': 'decimal'}),
            'expense_date': forms.DateInput(attrs={'type': 'date'}),
        }


class CustomerForm(CleanModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'phone', 'email', 'notes', 'is_active']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}


class PurchaseMetaForm(CleanModelForm):
    """Only purchase metadata is editable. Quantity/cost stay immutable after stock receipt."""
    class Meta:
        model = Purchase
        fields = ['supplier_name', 'invoice_number', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class ShopSettingsForm(CleanModelForm):
    class Meta:
        model = ShopSettings
        fields = ['name', 'logo', 'phone', 'address', 'receipt_footer', 'currency', 'low_stock_default']
        widgets = {
            'logo': forms.FileInput(attrs={'accept': 'image/*'}),
            'low_stock_default': forms.NumberInput(attrs={'step': 'any', 'inputmode': 'decimal'}),
        }


ROLE_CHOICES = [('admin', 'Admin'), ('cashier', 'Cashier')]
from .models import Branch


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))
    branch = forms.ModelChoiceField(queryset=Branch.objects.filter(is_active=True, name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA')), required=False, empty_label='Choose branch', widget=forms.Select(attrs={'class':'form-control'}))

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'password', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'toggle-input')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        data=super().clean()
        if data.get('role')=='cashier' and not data.get('branch'):
            self.add_error('branch','Assign a branch to every cashier.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
            user.profile.role = self.cleaned_data['role']
            user.profile.branch = self.cleaned_data.get('branch')
            user.profile.save(update_fields=['role','branch'])
        return user


class UserEditForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep current password'}),
        help_text='Leave blank if you do not want to change the password.'
    )
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))
    branch = forms.ModelChoiceField(queryset=Branch.objects.filter(is_active=True, name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA')), required=False, empty_label='Choose branch', widget=forms.Select(attrs={'class':'form-control'}))

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['role'].initial = getattr(self.instance.profile, 'role', 'cashier')
            self.fields['branch'].initial = getattr(self.instance.profile, 'branch_id', None)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'toggle-input')
            else:
                field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        data=super().clean()
        if data.get('role')=='cashier' and not data.get('branch'):
            self.add_error('branch','Assign a branch to every cashier.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        if commit:
            user.save()
            user.profile.role = self.cleaned_data['role']
            user.profile.branch = self.cleaned_data.get('branch')
            user.profile.save(update_fields=['role','branch'])
        return user
