"""Template-only flags. Permission checks are enforced in decorators and middleware."""
from .permissions import retail_admin, transport_member


def transport_role(request):
    member = transport_member(request)
    return {
        'transport_membership': member,
        'transport_is_manager': retail_admin(request.user) or bool(member and member.role == 'manager'),
        'transport_is_staff': bool(member and member.role == 'staff'),
    }
