"""Idempotent Razorpay webhook processing.

Always store the event first (deduplicated on the gateway event id), then process
it inside one transaction holding a lock on the event row, so two concurrent
deliveries of the same event cannot both run. A handler failure rolls back its
work and leaves the event unprocessed; the view returns 500 and Razorpay retries.

Handlers only translate payloads; all money movement goes through
``billing.fulfillment`` so the webhook and the checkout callback share one path.
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from . import fulfillment
from .models import PackOrder, Payment, Subscription, WebhookEvent

logger = logging.getLogger(__name__)


def process_event(event_id, event_type, payload):
    """Store-then-process a webhook event. Returns 'processed', 'duplicate' or
    'ignored'."""
    WebhookEvent.objects.get_or_create(
        event_id=event_id, defaults={'event_type': event_type, 'payload': payload})
    handler = _HANDLERS.get(event_type)
    with transaction.atomic():
        event = WebhookEvent.objects.select_for_update().get(event_id=event_id)
        if event.processed_at is not None:
            return 'duplicate'
        if handler:
            handler(payload)
        event.processed_at = timezone.now()
        event.save(update_fields=['processed_at'])
    return 'processed' if handler else 'ignored'


def _entity(payload, key):
    return ((payload or {}).get(key) or {}).get('entity') or {}


def _timestamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)


def _pack_order(entity):
    order_id = entity.get('order_id')
    if not order_id:
        return None
    return PackOrder.objects.filter(gateway_order_id=order_id).first()


def _payment_captured(payload):
    entity = _entity(payload, 'payment')
    pay_id = entity.get('id')
    if not pay_id:
        return
    order = _pack_order(entity)
    if order is not None:
        fulfillment.fulfill_pack_order(order, pay_id)
        return
    # Subscription payments are recorded by ``subscription.charged``, which also
    # carries the billing period. Anything else is unknown to us; reconciliation
    # reports a processed capture with no local Payment.
    if not (entity.get('subscription_id') or entity.get('invoice_id')):
        logger.warning('payment.captured %s matches no pack order', pay_id)


def _payment_failed(payload):
    entity = _entity(payload, 'payment')
    pay_id = entity.get('id')
    order = _pack_order(entity)
    if order is None or not pay_id:
        return
    fulfillment.mark_pack_order_failed(order)
    Payment.objects.get_or_create(
        razorpay_payment_id=pay_id,
        defaults={
            'user': order.user, 'payment_type': Payment.PACK,
            'razorpay_order_id': order.gateway_order_id or '',
            'amount': order.amount, 'currency': order.currency,
            'status': Payment.FAILED, 'pack': order.pack,
            'description': f"{order.pack.name} credit pack",
        })


def _subscription(payload):
    sub_id = _entity(payload, 'subscription').get('id')
    sub = Subscription.objects.filter(razorpay_subscription_id=sub_id).first() if sub_id else None
    if sub_id and sub is None:
        logger.warning('Webhook for unknown subscription %s', sub_id)
    return sub


def _subscription_charged(payload):
    sub = _subscription(payload)
    if sub is None:
        return
    sub_entity = _entity(payload, 'subscription')
    pay_entity = _entity(payload, 'payment')
    if not pay_entity.get('id'):
        logger.warning('subscription.charged for %s carried no payment', sub.razorpay_subscription_id)
        return
    fulfillment.record_subscription_charge(
        sub, pay_entity['id'],
        amount=fulfillment.from_minor_units(pay_entity['amount']) if pay_entity.get('amount') else None,
        currency=pay_entity.get('currency'),
        period_start=_timestamp(sub_entity.get('current_start')),
        period_end=_timestamp(sub_entity.get('current_end')),
        order_id=pay_entity.get('order_id', ''),
        gateway_plan_id=sub_entity.get('plan_id'),
    )


def _subscription_updated(payload):
    sub = _subscription(payload)
    if sub is not None:
        fulfillment.apply_plan_update(sub, _entity(payload, 'subscription').get('plan_id'))


def _set_subscription_status(status):
    def handler(payload):
        sub = _subscription(payload)
        if sub is not None and sub.status != status:
            sub.status = status
            sub.save(update_fields=['status', 'updated_at'])
    return handler


def _refund_processed(payload):
    entity = _entity(payload, 'refund')
    payment = Payment.objects.filter(razorpay_payment_id=entity.get('payment_id')).first()
    if payment is None or not entity.get('id'):
        logger.warning('refund.processed for unknown payment %s', entity.get('payment_id'))
        return
    fulfillment.apply_refund(payment, entity['id'], fulfillment.from_minor_units(entity.get('amount')))


_HANDLERS = {
    'payment.captured': _payment_captured,
    'payment.failed': _payment_failed,
    'subscription.charged': _subscription_charged,
    'subscription.updated': _subscription_updated,
    # Razorpay sends ``cancelled`` when the cancellation takes effect (at cycle
    # end when the user chose that), so the plan ends here, not on request.
    'subscription.cancelled': _set_subscription_status(Subscription.CANCELLED),
    'subscription.completed': _set_subscription_status(Subscription.EXPIRED),
    'subscription.halted': _set_subscription_status(Subscription.PAST_DUE),
    'subscription.paused': _set_subscription_status(Subscription.PAST_DUE),
    'subscription.resumed': _set_subscription_status(Subscription.ACTIVE),
    'refund.processed': _refund_processed,
}
