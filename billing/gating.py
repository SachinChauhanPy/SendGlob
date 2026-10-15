"""Feature and credit gating. Backend-authoritative, per the project rule that
business-rule restrictions always have server-side enforcement.

Tier is derived, never stored: an active Subscription -> that plan's tier; else if
the user has ever bought a pack -> "paid PAYG"; else "free".
"""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from . import conf, wallet
from .models import Payment, Subscription


def get_tier(user):
    """Return ('free' | 'payg' | plan.tier, plan_or_None)."""
    if not user or not user.is_authenticated:
        return 'free', None
    sub = (Subscription.objects.filter(user=user, status=Subscription.ACTIVE)
           .select_related('plan').first())
    if sub:
        return sub.plan.tier, sub.plan
    if Payment.objects.filter(user=user, status=Payment.CAPTURED, payment_type=Payment.PACK).exists():
        return 'payg', None
    return 'free', None


def active_plan(user):
    return get_tier(user)[1]


def is_free_tier(user):
    return get_tier(user)[0] == 'free'


def require_credits(amount):
    """View decorator: block the request unless the user can afford ``amount``
    credits. Backend guard for credit-consuming entry points."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not wallet.can_afford(request.user, amount):
                raise PermissionDenied('Insufficient credits.')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


class PlanFeatureRequiredMixin(AccessMixin):
    """CBV mixin gating a plan-only feature. Set ``required_feature`` to a Plan
    boolean field name (e.g. 'recurring_scheduling'). Free/PAYG users without the
    feature are denied."""

    required_feature = None

    def dispatch(self, request, *args, **kwargs):
        plan = active_plan(request.user)
        if not plan or not getattr(plan, self.required_feature, False):
            raise PermissionDenied('This feature requires a plan that includes it.')
        return super().dispatch(request, *args, **kwargs)


def can_use_recurring_scheduling(user):
    plan = active_plan(user)
    return bool(plan and plan.recurring_scheduling)


def max_smtp_configs(user):
    plan = active_plan(user)
    return plan.max_smtp_configs if plan else 1


def recipient_cap(user):
    """Recipients-per-run cap. Free tier is capped; paid tiers are uncapped
    (return None)."""
    if is_free_tier(user):
        return conf.free_tier_recipient_cap()
    return None
