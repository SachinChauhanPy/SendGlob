"""Razorpay gateway isolation.

Every Razorpay call lives here. Two modes:

- **Live** (keys + SDK present): calls go to Razorpay. A failed call raises
  ``GatewayError``; it never degrades into a fake order, because a stub id handed
  to the real checkout widget only produces a broken payment popup.
- **Manual** (no keys or SDK): order/subscription creation returns a clearly
  marked ``manual_`` stub so an operator can settle payment out of band and
  fulfil it with the ``fulfill_pack_order`` / ``activate_plan`` commands.

Signature verification only needs the key secret and always works via hmac.
"""
import hashlib
import hmac
import logging
import uuid

from . import conf

logger = logging.getLogger(__name__)

MANUAL_PREFIX = 'manual_'

# Razorpay requires a finite charge count per subscription. Roughly ten years of
# renewals; the subscription completes (and is expired locally) after that.
_SUBSCRIPTION_TOTAL_COUNT = {'monthly': 120, 'yearly': 10}


class GatewayError(Exception):
    """A live gateway call failed. Callers show a retry message to the user."""


def _client():
    """Return a razorpay.Client when the SDK and keys are present, else None."""
    key_id, key_secret, _ = conf.razorpay_keys()
    if not key_id or not key_secret:
        return None
    try:
        import razorpay
    except ImportError:
        return None
    return razorpay.Client(auth=(key_id, key_secret))


def is_live():
    """True when a real gateway client is available (keys + SDK)."""
    return _client() is not None


def to_minor_units(amount):
    """Major units (rupees) to the integer minor units Razorpay expects (paise)."""
    return int(round(float(amount) * 100))


def create_order(amount, currency, receipt, notes=None):
    """Create a one-time payment Order and return a dict with at least ``id``."""
    payload = {
        'amount': to_minor_units(amount),
        'currency': currency,
        'receipt': receipt[:40],
        'notes': notes or {},
    }
    client = _client()
    if client is None:
        return {**payload, 'id': f"{MANUAL_PREFIX}order_{receipt[:32]}", 'stub': True}
    try:
        return client.order.create(payload)
    except Exception as exc:
        logger.exception("Razorpay create_order failed: %s", exc)
        raise GatewayError(str(exc)) from exc


def create_subscription(razorpay_plan_id, billing_cycle, notes=None):
    """Create a recurring Subscription for a gateway plan id."""
    client = _client()
    if client is None:
        return {'id': f"{MANUAL_PREFIX}sub_{uuid.uuid4().hex[:20]}", 'stub': True}
    if not razorpay_plan_id:
        raise GatewayError('Plan is not linked to a Razorpay plan id.')
    try:
        return client.subscription.create({
            'plan_id': razorpay_plan_id,
            'total_count': _SUBSCRIPTION_TOTAL_COUNT.get(billing_cycle, 120),
            'notes': notes or {},
        })
    except Exception as exc:
        logger.exception("Razorpay create_subscription failed: %s", exc)
        raise GatewayError(str(exc)) from exc


def cancel_subscription(subscription_id, cancel_at_period_end=True):
    """Cancel a recurring Subscription in Razorpay. Manual subscriptions have no
    gateway counterpart, so there is nothing to call."""
    if not subscription_id or subscription_id.startswith(MANUAL_PREFIX):
        return {'id': subscription_id, 'cancelled': True, 'stub': True}
    client = _client()
    if client is None:
        raise GatewayError('Gateway is not configured; cannot cancel a live subscription.')
    try:
        return client.subscription.cancel(
            subscription_id, {'cancel_at_cycle_end': 1 if cancel_at_period_end else 0})
    except Exception as exc:
        logger.exception("Razorpay cancel_subscription failed for %s: %s", subscription_id, exc)
        raise GatewayError(str(exc)) from exc


def create_plan(name, amount, currency, billing_cycle):
    """Create a Razorpay plan and return its id. Razorpay plans are immutable,
    so a price change means a new plan; subscribers on the old one keep it."""
    client = _client()
    if client is None:
        raise GatewayError('Gateway is not configured; cannot create a Razorpay plan.')
    try:
        return client.plan.create({
            'period': billing_cycle,
            'interval': 1,
            'item': {'name': name, 'amount': to_minor_units(amount), 'currency': currency},
        })['id']
    except Exception as exc:
        logger.exception("Razorpay create_plan failed: %s", exc)
        raise GatewayError(str(exc)) from exc


def schedule_plan_change(subscription_id, razorpay_plan_id):
    """Move a subscription to another gateway plan from its next renewal."""
    if subscription_id.startswith(MANUAL_PREFIX):
        return {'id': subscription_id, 'stub': True}
    client = _client()
    if client is None:
        raise GatewayError('Gateway is not configured; cannot change a live subscription.')
    if not razorpay_plan_id:
        raise GatewayError('Target plan is not linked to a Razorpay plan id.')
    try:
        return client.subscription.edit(subscription_id, {
            'plan_id': razorpay_plan_id,
            'schedule_change_at': 'cycle_end',
            'customer_notify': 1,
        })
    except Exception as exc:
        logger.exception("Razorpay plan change failed for %s: %s", subscription_id, exc)
        raise GatewayError(str(exc)) from exc


def cancel_scheduled_plan_change(subscription_id):
    """Withdraw a pending plan change."""
    if subscription_id.startswith(MANUAL_PREFIX):
        return {'id': subscription_id, 'stub': True}
    client = _client()
    if client is None:
        raise GatewayError('Gateway is not configured; cannot change a live subscription.')
    try:
        return client.subscription.cancel_scheduled_changes(subscription_id)
    except Exception as exc:
        logger.exception("Razorpay cancel_scheduled_changes failed for %s: %s", subscription_id, exc)
        raise GatewayError(str(exc)) from exc


def _hmac_matches(secret, message, signature):
    if not secret:
        return False
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def verify_payment_signature(order_id, payment_id, signature):
    """Checkout callback for an Order: HMAC_SHA256(order_id|payment_id, key_secret)."""
    _, key_secret, _ = conf.razorpay_keys()
    return _hmac_matches(key_secret, f"{order_id}|{payment_id}".encode(), signature)


def verify_subscription_signature(subscription_id, payment_id, signature):
    """Checkout callback for a Subscription: HMAC_SHA256(payment_id|subscription_id)."""
    _, key_secret, _ = conf.razorpay_keys()
    return _hmac_matches(key_secret, f"{payment_id}|{subscription_id}".encode(), signature)


def verify_webhook_signature(body_bytes, signature):
    """Verify a webhook payload signature against the webhook secret."""
    _, _, webhook_secret = conf.razorpay_keys()
    return _hmac_matches(webhook_secret, body_bytes, signature)
