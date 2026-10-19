"""HAMA NA KILASI accounts: department role, no retail branch assignment."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import TransportAccess

class TransportUserForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class':'form-control'}))
    first_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class':'form-control','autocomplete':'new-password'}))
    role = forms.ChoiceField(choices=TransportAccess.ROLES, widget=forms.Select(attrs={'class':'form-control'}))
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
    role = forms.ChoiceField(choices=TransportAccess.ROLES, widget=forms.Select(attrs={'class':'form-control'}))
    is_active = forms.BooleanField(required=False, initial=True)
    new_password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={'class':'form-control','autocomplete':'new-password'}))
    def clean_new_password(self):
        value = self.cleaned_data.get('new_password')
        if value:
            validate_password(value)
        return value
