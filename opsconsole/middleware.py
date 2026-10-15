"""Operations console middleware.

``MaintenanceModeMiddleware`` enforces the maintenance-mode operational flag: when
on, non-staff write requests are blocked with a friendly 503 page while reads and
all staff activity continue. ``ImpersonationMiddleware`` powers operator
"view as user" sessions (see views) - read-only, time-boxed, and bannered.
"""
from django.shortcuts import render
from django.utils import timezone

from . import flags

# Methods that mutate state. Reads stay available during maintenance.
_UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

# Paths that must keep working for non-staff even during maintenance: auth flows
# (so users can log in/out) and the console itself.
_MAINTENANCE_EXEMPT_PREFIXES = ('/accounts/', '/ops/', '/admin/')


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_block(request):
            return render(request, 'opsconsole/maintenance.html', status=503)
        return self.get_response(request)

    def _should_block(self, request):
        if request.method not in _UNSAFE_METHODS:
            return False
        if not flags.is_maintenance():
            return False
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_staff:
            return False
        if any(request.path.startswith(p) for p in _MAINTENANCE_EXEMPT_PREFIXES):
            return False
        return True


class ImpersonationMiddleware:
    """Swap ``request.user`` to an operator-selected target for read-only viewing.

    Activated by ``opsconsole.views.impersonate_start`` which stores the target
    and an expiry in the session. While active:
      * ``request.user`` becomes the target user (so pages render as they see them)
      * ``request.impersonator`` holds the real operator
      * write requests are refused (the session is read-only by design)
      * the session auto-expires after a fixed window
    Start and stop are audit-logged in the views.
    """

    SESSION_KEY = 'ops_impersonate'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.impersonator = None
        data = request.session.get(self.SESSION_KEY)
        if data and self._activate(request, data):
            # The "stop impersonating" action is the one write we must allow, so
            # the operator can always end the session.
            if request.method in _UNSAFE_METHODS and not request.path.endswith('/impersonate/stop/'):
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden(
                    'Impersonation is read-only. Stop impersonating to make changes.')
        return self.get_response(request)

    def _activate(self, request, data):
        # Expired? Clear and behave as if not impersonating.
        expires = data.get('expires')
        if not expires or timezone.now().timestamp() > float(expires):
            request.session.pop(self.SESSION_KEY, None)
            return False
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            target = User.objects.get(pk=data.get('target_id'))
        except User.DoesNotExist:
            request.session.pop(self.SESSION_KEY, None)
            return False
        operator = getattr(request, 'user', None)
        if operator is None or not operator.is_authenticated:
            return False
        request.impersonator = operator
        request.user = target
        return True
