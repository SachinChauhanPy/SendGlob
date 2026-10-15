"""Deployment checks for the payment gateway (run by ``manage.py check --deploy``
and at startup). A misconfigured gateway silently falls back to manual mode or
drops every webhook, so it is surfaced loudly instead."""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.db import DatabaseError

from . import conf, gateway


@register(Tags.security, deploy=True)
def check_gateway_configuration(app_configs, **kwargs):
    key_id, key_secret, webhook_secret = conf.razorpay_keys()
    issues = []
    if bool(key_id) != bool(key_secret):
        issues.append(Error('RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set together.',
                            id='billing.E001'))
    if key_id and key_secret:
        try:
            import razorpay  # noqa: F401
        except ImportError:
            issues.append(Error('Razorpay keys are set but the razorpay package is not '
                                'installed; checkout would run in manual mode.',
                                id='billing.E002'))
        if not webhook_secret:
            issues.append(Error('RAZORPAY_WEBHOOK_SECRET is empty; every webhook would be '
                                'rejected and renewals, failures and refunds never recorded.',
                                id='billing.E003'))
        if not settings.DEBUG and key_id.startswith('rzp_test_'):
            issues.append(Warning('Razorpay test keys are configured with DEBUG off.',
                                  id='billing.W002'))
    if gateway.is_live():
        from .models import Plan
        try:
            unlinked = list(Plan.objects.filter(is_active=True, razorpay_plan_id='')
                            .values_list('tier', flat=True))
        except DatabaseError:
            unlinked = []  # tables not migrated yet
        if unlinked:
            issues.append(Warning(f"Active plans with no Razorpay plan id cannot be bought: "
                                  f"{', '.join(unlinked)}.",
                                  hint='Run manage.py sync_razorpay_plans.', id='billing.W003'))
    if not settings.DEBUG and not gateway.is_live():
        issues.append(Warning('Payment gateway not configured: checkout runs in manual mode '
                              '(orders are fulfilled with fulfill_pack_order / activate_plan).',
                              id='billing.W001'))
    return issues
