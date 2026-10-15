"""Backend-authoritative access control for the operations console.

Two gates, both enforced server-side (never UI-only):

* ``staff_member_required`` - any ``is_staff`` user may view the read-only
  console (Phase A is entirely read-only).
* ``operator_required`` - destructive control actions (later phases) additionally
  require ``is_superuser`` or membership in the ``ops_operators`` group.

A non-staff user hitting ``/ops/`` gets a 404 rather than a 403 so the console's
existence is not advertised to ordinary users.
"""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.http import Http404

OPERATOR_GROUP = 'ops_operators'


def _is_staff(user):
    return bool(user and user.is_authenticated and user.is_staff)


def is_operator(user):
    """True if the user may perform destructive control actions."""
    if not _is_staff(user):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=OPERATOR_GROUP).exists()


def staff_member_required(view_func):
    """Restrict a view to staff. Non-staff get a 404 (console stays hidden)."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _is_staff(request.user):
            raise Http404
        return view_func(request, *args, **kwargs)

    return _wrapped


def operator_required(view_func):
    """Restrict a view to operators (superuser or ops_operators group)."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_operator(request.user):
            raise Http404
        return view_func(request, *args, **kwargs)

    return _wrapped


class StaffRequiredMixin(AccessMixin):
    """Class-based-view equivalent of :func:`staff_member_required`."""

    def dispatch(self, request, *args, **kwargs):
        if not _is_staff(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def client_ip(request):
    """Best-effort client IP for audit rows (honours a single proxy hop)."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
