"""Transport privileges. Never infer transport access from the retail cashier role."""
from functools import wraps
from django.http import HttpResponseForbidden
from django.shortcuts import redirect


def retail_admin(user):
    return bool(user.is_authenticated and (user.is_superuser or getattr(getattr(user, 'profile', None), 'role', None) == 'admin'))


def transport_member(request):
    """One HAMA NA KILASI department. Retail branch never grants or limits access."""
    if not request.user.is_authenticated or not request.user.is_active:
        return None
    if retail_admin(request.user):
        return None
    member = getattr(request, 'transport_access', None)
    if member is None:
        from .models import TransportAccess
        member = TransportAccess.objects.filter(user_id=request.user.pk).first()
    if not member or not member.is_active:
        return None
    # Retail UserProfile remains cashier only for compatibility with old login;
    # the firewall still blocks every Retail endpoint for Transport identities.
    return member


def _requires(required):
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if retail_admin(request.user):
                return view(request, *args, **kwargs)
            member = transport_member(request)
            if member and ((required == 'staff' and member.role in ('staff','manager')) or
                       (required == 'manager' and member.role == 'manager')):
                return view(request, *args, **kwargs)
            return HttpResponseForbidden('You do not have permission for this Transport operation.')
        return wrapped
    return deco


transport_staff_required = _requires('staff')
transport_manager_required = _requires('manager')


def transport_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not retail_admin(request.user):
            return HttpResponseForbidden('Only business administrators can manage Transport accounts.')
        return view(request, *args, **kwargs)
    return wrapped
