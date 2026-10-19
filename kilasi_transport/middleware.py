"""Prevent transport-only accounts from using retail, SMS, billing, and Django admin endpoints."""
from django.http import HttpResponseForbidden
from .models import TransportAccess
from .permissions import retail_admin


class TransportRouteFirewall:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.transport_access = None
        user = request.user
        if user.is_authenticated and not retail_admin(user):
            access = TransportAccess.objects.select_related('branch', 'driver').filter(user_id=user.pk).first()
            if access:
                # Keep account relation available even for suspended transport users;
                # never allow them to fall through to retail cashier privileges.
                request.transport_access = access
                path = request.path_info
                # Driver role is strictly limited to the driver workspace.
                if access.role == 'driver':
                    driver_path = path.startswith('/transport/driver/')
                    shared_path = path in ('/login/', '/logout/', '/favicon.ico') or (
                        request.method in ('GET', 'HEAD') and
                        (path.startswith('/static/') or path.startswith('/media/branding/'))
                    )
                    if not driver_path and not shared_path:
                        return HttpResponseForbidden('Drivers can only open their own Transport workspace.')
                    if driver_path and (not access.is_active or not access.driver_id or
                            access.driver.status != 'active'):
                        return HttpResponseForbidden('Driver account not active or not linked.')
                permitted = (path.startswith('/transport/') or path in ('/login/', '/logout/', '/favicon.ico')
                             or (request.method in ('GET','HEAD') and
                                 (path.startswith('/static/') or path.startswith('/media/branding/'))))
                if not permitted:
                    return HttpResponseForbidden('Transport accounts cannot open Retail, SMS, billing or administration.')
                if path.startswith('/transport/') and not access.is_active:
                    return HttpResponseForbidden('Transport access is suspended. Contact the administrator.')
        return self.get_response(request)
