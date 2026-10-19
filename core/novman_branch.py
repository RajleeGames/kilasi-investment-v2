from django.core.exceptions import PermissionDenied
from .models import Branch


def is_admin(user):
    return bool(user.is_superuser or getattr(getattr(user, 'profile', None), 'role', '') == 'admin')


def current_branch(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in to use Kilasi Investment.')
    active = Branch.objects.filter(is_active=True, name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA'))
    if is_admin(request.user):
        requested = request.session.get('novman_branch_id')
        branch = active.filter(pk=requested).first() if requested else None
        if branch is None:
            # Initial admin context only. The admin can switch branches explicitly.
            branch = active.order_by('legacy_novman_id', 'id').first()
            if branch:
                request.session['novman_branch_id'] = branch.pk
        if branch is None:
            raise PermissionDenied('No active Kilasi Investment branch is configured.')
        return branch
    profile = getattr(request.user, 'profile', None)
    assigned_id = getattr(profile, 'branch_id', None)
    branch = active.filter(pk=assigned_id).first() if assigned_id else None
    if branch is None:
        raise PermissionDenied('No active branch assigned. Ask an admin to edit your account.')
    return branch


def branch_context(request):
    if not request.user.is_authenticated:
        return {'novman_branch': None, 'novman_branches': [], 'novman_can_switch_branch': False}
    try:
        branch = current_branch(request)
    except PermissionDenied:
        return {'novman_branch': None, 'novman_branches': [], 'novman_can_switch_branch': False}
    admin = is_admin(request.user)
    return {
        'novman_branch': branch,
        'novman_branches': Branch.objects.filter(is_active=True, name__in=('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA')).order_by('legacy_novman_id', 'name') if admin else [],
        'novman_can_switch_branch': admin,
    }
