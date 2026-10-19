"""HAMA NA KILASI accounts: department role, no retail branch assignment."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import Driver, TransportAccess

class TransportUserForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class':'form-control'}))
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class':'form-control','autocomplete':'new-password'}))
    role = forms.ChoiceField(choices=TransportAccess.ROLES, widget=forms.Select(attrs={'class':'form-control'}))
    driver = forms.ModelChoiceField(queryset=Driver.objects.filter(status='active'), required=False,
        widget=forms.Select(attrs={'class':'form-control'}),
        help_text='Select an active driver when the role is Transport Driver.')
    def clean(self):
        data = super().clean()
        if data.get('role') == 'driver':
            driver = data.get('driver')
            if driver is None:
                self.add_error('driver', 'Choose the driver this account belongs to.')
            elif TransportAccess.objects.filter(driver=driver).exists():
                self.add_error('driver', 'This driver already has a login. Edit that account instead.')
        return data
    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        if get_user_model().objects.filter(username__iexact=value).exists():
            raise forms.ValidationError('Username already exists.')
        return value
    def clean_password(self):
        value = self.cleaned_data['password']
        validate_password(value)
        return value

class TransportAccessEditForm(forms.Form):
    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        if access and access.driver_id:
            self.fields['driver'].queryset = Driver.objects.filter(pk=access.driver_id) | Driver.objects.filter(status='active')
    role = forms.ChoiceField(choices=TransportAccess.ROLES, widget=forms.Select(attrs={'class':'form-control'}))
    is_active = forms.BooleanField(required=False, initial=True)
    driver = forms.ModelChoiceField(queryset=Driver.objects.filter(status='active'), required=False,
        widget=forms.Select(attrs={'class':'form-control'}),
        help_text='Required for Transport Driver accounts.')
    new_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class':'form-control','autocomplete':'new-password'}))
    def clean(self):
        data = super().clean()
        if data.get('role') == 'driver':
            driver = data.get('driver')
            if driver is None or driver.status != 'active':
                self.add_error('driver', 'Select an active driver.')
            elif TransportAccess.objects.filter(driver=driver).exclude(pk=getattr(self.access, 'pk', None)).exists():
                self.add_error('driver', 'This driver belongs to another account.')
        return data
    def clean_new_password(self):
        value = self.cleaned_data.get('new_password')
        if value:
            validate_password(value)
        return value
