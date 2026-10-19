"""Reuse the original Novman login form, but redirect transport accounts correctly."""
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.http import HttpResponseForbidden
from core.views import login_view as novman_login
from .models import TransportAccess


def login_view(request):
    if request.user.is_authenticated:
        access = TransportAccess.objects.filter(user_id=request.user.pk).first()
        if access and not request.user.is_superuser and getattr(getattr(request.user, 'profile', None), 'role', None) != 'admin':
            return redirect('transport:driver_home' if access.role == 'driver' else 'transport:home') if access.is_active else HttpResponseForbidden('Transport account suspended.')
    response = novman_login(request)
    if request.method == 'POST' and request.user.is_authenticated:
        access = TransportAccess.objects.filter(user_id=request.user.pk).first()
        if access and not request.user.is_superuser and getattr(getattr(request.user, 'profile', None), 'role', None) != 'admin':
            if not access.is_active:
                logout(request)
                messages.error(request, 'This Transport account is suspended.')
                return redirect('login')
            return redirect('transport:driver_home' if access.role == 'driver' else 'transport:home')
    return response
