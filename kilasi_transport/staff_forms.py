"""Staff booking request: no quoting amounts or linking a trip."""
from django import forms
from .forms import StyledForm
from .models import Booking


class StaffBookingForm(StyledForm):
    class Meta:
        model = Booking
        fields = ['customer_name','customer_phone','origin','destination',
                  'requested_date','cargo_details','notes']
        widgets = {'requested_date': forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),
                   'notes':forms.Textarea(attrs={'rows':3})}

    def __init__(self,*args,**kwargs):
        self.branch=kwargs.pop('branch', None)
        super().__init__(*args,**kwargs)
