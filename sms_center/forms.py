from django import forms

from .models import SmsContact, SmsSenderProfile, SmsTemplate
from .services import normalize_phone


class SmsContactForm(forms.ModelForm):
    class Meta:
        model = SmsContact
        fields = ["name", "phone", "group_name", "sms_allowed", "is_active", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Customer or contact name"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "0712 345 678 or 255712345678"}),
            "group_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. VIP customers"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional notes"}),
            "sms_allowed": forms.CheckboxInput(attrs={"class": "toggle-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "toggle-input"}),
        }

    def clean_phone(self):
        raw = self.cleaned_data["phone"]
        normalized = normalize_phone(raw)
        if not normalized:
            raise forms.ValidationError("Enter a valid phone number.")
        self.instance.normalized_phone = normalized
        return raw.strip()

    def clean(self):
        cleaned = super().clean()
        normalized = getattr(self.instance, "normalized_phone", "")
        if normalized:
            duplicate = SmsContact.objects.filter(normalized_phone=normalized)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error("phone", "This phone number already exists in SMS contacts.")
        return cleaned


class SmsTemplateForm(forms.ModelForm):
    class Meta:
        model = SmsTemplate
        fields = ["name", "category", "body", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Template name"}),
            "category": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Promotion, Reminder"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 7, "placeholder": "Hello {name}, ..."}),
            "is_active": forms.CheckboxInput(attrs={"class": "toggle-input"}),
        }


class SmsSenderProfileForm(forms.ModelForm):
    class Meta:
        model = SmsSenderProfile
        fields = ["label", "sender_id", "is_default", "is_active"]
        widgets = {
            "label": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Novman SMS sender"}),
            "sender_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "YOUR_ID", "maxlength": 11}),
            "is_default": forms.CheckboxInput(attrs={"class": "toggle-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "toggle-input"}),
        }

    def clean_sender_id(self):
        return self.cleaned_data["sender_id"].strip().upper()
