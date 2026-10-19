from django.core.exceptions import PermissionDenied
from core.novman_branch import current_branch
from kilasi_transport.permissions import retail_admin, transport_member


def scope(request):
    """Require department-specific server-side access, including active branch assignment."""
    if not request.user.is_authenticated or not request.user.is_active:
        raise PermissionDenied('Sign in using an active account.')
    ns = request.resolver_match.namespace
    if ns == 'retail_sms':
        if not retail_admin(request.user):
            raise PermissionDenied('Retail SMS is for business administrators.')
        department = 'retail'
    elif ns == 'transport_sms':
        member = transport_member(request)
        if not retail_admin(request.user) and not (member and member.role == 'manager'):
            raise PermissionDenied('Transport SMS is for Transport Managers.')
        department = 'transport'
    else:
        raise PermissionDenied('Unknown SMS department.')
    # Retail keeps branch scoping; Transport is one independent department.
    return department, (current_branch(request) if department == 'retail' else None)
