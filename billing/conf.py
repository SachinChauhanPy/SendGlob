"""Tunable billing configuration, read from Django settings via getattr so the
defaults work out of the box and can be overridden without code changes."""
from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


def free_signup_credits():
    return int(_get('BILLING_FREE_SIGNUP_CREDITS', 50))


def infra_cost_floor():
    """Monthly infrastructure cost the operator wants revenue to cover."""
    return float(_get('BILLING_INFRA_COST_FLOOR', 0))


def receipt_prefix():
    return _get('BILLING_RECEIPT_PREFIX', 'SG')


def razorpay_keys():
    """(key_id, key_secret, webhook_secret) from settings; any may be blank."""
    return (
        _get('RAZORPAY_KEY_ID', ''),
        _get('RAZORPAY_KEY_SECRET', ''),
        _get('RAZORPAY_WEBHOOK_SECRET', ''),
    )


def free_tier_recipient_cap():
    """Max recipients per run on the free tier (server-enforced)."""
    return int(_get('BILLING_FREE_RECIPIENT_CAP', 50))


def seller_details():
    """Issuer identity printed on receipts (name, multi-line address, contact
    email, tax id). Blank values are simply omitted from the document."""
    return {
        'name': _get('BILLING_SELLER_NAME', 'SendGlob'),
        'address': _get('BILLING_SELLER_ADDRESS', ''),
        'email': _get('BILLING_SELLER_EMAIL', ''),
        'tax_id': _get('BILLING_SELLER_TAX_ID', ''),
    }


def pending_checkout_ttl_hours():
    """How long an unpaid pack order or pending subscription stays open."""
    return int(_get('BILLING_PENDING_CHECKOUT_TTL_HOURS', 24))


def subscription_grace_days():
    """Days past ``current_period_end`` before an un-renewed ACTIVE subscription
    is expired locally. Covers delayed webhooks so paying users are never cut off
    early; manual (gateway-less) plans rely on this to end."""
    return int(_get('BILLING_SUBSCRIPTION_GRACE_DAYS', 3))
