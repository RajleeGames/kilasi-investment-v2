from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect

def admin_required(view):
    @wraps(view)
    def wrapper(request,*args,**kwargs):
        if not request.user.is_authenticated: return redirect('login')
        role=getattr(getattr(request.user,'profile',None),'role','cashier')
        if role!='admin' and not request.user.is_superuser:
            messages.error(request,'Admin access is required for this page.')
            return redirect('pos')
        return view(request,*args,**kwargs)
    return wrapper
