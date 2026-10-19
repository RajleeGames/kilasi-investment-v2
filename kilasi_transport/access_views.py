"""Admin only HAMA NA KILASI accounts — no Retail branch coupling."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .access_forms import TransportUserForm, TransportAccessEditForm
from .models import TransportAccess
from .permissions import transport_admin_required

@transport_admin_required
@require_http_methods(['GET','POST'])
def users(request):
    form = TransportUserForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                username=data['username'], password=data['password'],
                first_name=data['first_name'], last_name=data['last_name'],
                is_staff=False, is_superuser=False)
            profile = user.profile
            profile.role = 'cashier'  # Compatibility only, never grants Retail access.
            profile.branch = None
            profile.save(update_fields=['role','branch'])
            TransportAccess.objects.create(user=user, branch=None, role=data['role'], is_active=True)
        messages.success(request, 'HAMA NA KILASI account created.')
        return redirect('transport:users')
    return render(request, 'kilasi_transport/users.html', {
        'form': form,
        'accounts': TransportAccess.objects.select_related('user').order_by('user__username'),
    })

@transport_admin_required
@require_http_methods(['GET','POST'])
def user_edit(request, pk):
    access = get_object_or_404(TransportAccess.objects.select_related('user'), pk=pk)
    user = access.user
    if user.is_superuser or user.is_staff or getattr(user.profile, 'role', '') == 'admin':
        return HttpResponseForbidden('Elevated accounts cannot be managed as Transport-only accounts.')
    form = TransportAccessEditForm(request.POST or None,
              initial={'role':access.role,'is_active':access.is_active})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            access.role = data['role']
            access.is_active = data['is_active']
            access.save(update_fields=['role','is_active'])
            user.is_active = data['is_active']
            fields = ['is_active']
            if data['new_password']:
                user.set_password(data['new_password'])
                fields.append('password')
            user.save(update_fields=fields)
            profile = user.profile
            profile.role = 'cashier'
            profile.branch = None
            profile.save(update_fields=['role','branch'])
        messages.success(request, 'HAMA NA KILASI account updated.')
        return redirect('transport:users')
    return render(request, 'kilasi_transport/user_edit.html', {'form':form,'account':access})
