"""Payment fulfilment: the single path by which money becomes credits and plans.

Webhooks, the checkout signature callback, and the manual operator commands all
call into here, so a payment is recorded, credited and receipted exactly once no
matter which of them arrives first. Every function runs in one transaction and
locks the row it fulfils, making concurrent duplicate deliveries safe.
"""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from . import receipts, wallet
from .models import CreditTransaction, PackOrder, Payment, Plan, Refund, Subscription

logger = logging.getLogger(__name__)


def from_minor_units(value):
    """Gateway integer minor units (paise) to a 2dp Decimal in major units."""
    return (Decimal(int(value or 0)) / 100).quantize(Decimal('0.01'))


def _grant_once(user, amount, *, transaction_type, reference_id, note):
    """Post a ledger credit unless one already exists for this reference."""
    if amount <= 0:
        return
    if CreditTransaction.objects.filter(user=user, transaction_type=transaction_type,
                                        reference_id=reference_id).exists():
        return
    wallet.credit(user, amount, transaction_type=transaction_type,
                  reference_id=reference_id, note=note)


def _capture_payment(payment_id, defaults):
    """Return a CAPTURED Payment for ``payment_id``, creating it or promoting an
    earlier FAILED row (Razorpay can capture a late authorisation after
    reporting the attempt as failed)."""
    payment = Payment.objects.select_for_update().filter(razorpay_payment_id=payment_id).first()
    if payment is None:
        return Payment.objects.create(razorpay_payment_id=payment_id, status=Payment.CAPTURED,
                                      paid_at=timezone.now(), **defaults)
    if payment.status == Payment.FAILED:
        for field, value in defaults.items():
            setattr(payment, field, value)
        payment.status = Payment.CAPTURED
        payment.paid_at = timezone.now()
        payment.save()
    return payment


def fulfill_pack_order(order, payment_id):
    """Mark a PackOrder paid: record the Payment, credit the snapshot amount and
    issue a receipt. Idempotent; returns the Payment."""
    with transaction.atomic():
        order = PackOrder.objects.select_for_update().select_related('pack', 'user').get(pk=order.pk)
        if order.status == PackOrder.PAID and order.payment_id:
            return order.payment
        payment = _capture_payment(payment_id, {
            'user': order.user,
            'payment_type': Payment.PACK,
            'razorpay_order_id': order.gateway_order_id or '',
            'amount': order.amount,
            'currency': order.currency,
            'credits_granted': order.credits,
            'description': f"{order.pack.name} credit pack",
            'pack': order.pack,
        })
        _grant_once(order.user, order.credits,
                    transaction_type=CreditTransaction.TYPE_PACK_PURCHASE,
                    reference_id=payment_id, note=f"{order.pack.name} purchase")
        receipts.issue_receipt(payment)
        order.status = PackOrder.PAID
        order.payment = payment
        order.paid_at = payment.paid_at
        order.save(update_fields=['status', 'payment', 'paid_at'])
        return payment


def mark_pack_order_failed(order):
    """Record a failed attempt. A paid order is never downgraded."""
    PackOrder.objects.filter(pk=order.pk, status=PackOrder.CREATED).update(status=PackOrder.FAILED)


def default_period_end(start, plan):
    return start + timedelta(days=365 if plan.billing_cycle == Plan.YEARLY else 30)


def _apply_scheduled_change(sub, gateway_plan_id=None, switch_now=False):
    """Move ``sub`` (a locked row) onto its scheduled plan when the gateway
    reports it is now billed on the scheduled gateway plan, or when an operator
    renews a manual plan (``switch_now``). Returns True if the plan changed."""
    if not sub.scheduled_plan_id:
        return False
    if not switch_now and not (gateway_plan_id and gateway_plan_id == sub.scheduled_gateway_plan_id):
        return False
    sub.plan = sub.scheduled_plan
    sub.gateway_plan_id = sub.scheduled_gateway_plan_id
    sub.scheduled_plan = None
    sub.scheduled_gateway_plan_id = ''
    return True


def apply_plan_update(subscription, gateway_plan_id):
    """``subscription.updated``: the gateway switched plans at cycle end."""
    with transaction.atomic():
        sub = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if _apply_scheduled_change(sub, gateway_plan_id):
            sub.save(update_fields=['plan', 'gateway_plan_id', 'scheduled_plan',
                                    'scheduled_gateway_plan_id', 'updated_at'])


def schedule_plan_change(subscription, plan):
    """Record a requested switch to ``plan`` from the next renewal. The caller
    has already registered it with the gateway (for gateway subscriptions)."""
    Subscription.objects.filter(pk=subscription.pk).update(
        scheduled_plan=plan,
        scheduled_gateway_plan_id='' if subscription.is_manual else plan.razorpay_plan_id,
        updated_at=timezone.now())


def clear_plan_change(subscription):
    Subscription.objects.filter(pk=subscription.pk).update(
        scheduled_plan=None, scheduled_gateway_plan_id='', updated_at=timezone.now())


def record_subscription_charge(subscription, payment_id, *, amount=None, currency=None,
                               period_start=None, period_end=None, order_id='',
                               gateway_plan_id=None, apply_scheduled_change=False):
    """Activate/renew a subscription for one billing cycle: record the Payment,
    grant the cycle's credits and issue a receipt. Idempotent per payment id.

    A scheduled plan switch is applied first when the gateway charged the
    scheduled gateway plan (``gateway_plan_id``) or the operator renews a manual
    plan onto it (``apply_scheduled_change``), so the renewal is billed and
    credited as the new plan.

    Gateway period timestamps, when given, always win (``subscription.charged``
    carries the authoritative ones). Without them a period starting now is
    assumed, but only for a charge not seen before, so a replayed or late
    checkout callback never shifts a period the webhook already set."""
    with transaction.atomic():
        sub = (Subscription.objects.select_for_update().select_related('plan', 'user')
               .get(pk=subscription.pk))
        already_recorded = Payment.objects.filter(
            razorpay_payment_id=payment_id, status=Payment.CAPTURED).exists()
        if not already_recorded:
            _apply_scheduled_change(sub, gateway_plan_id, switch_now=apply_scheduled_change)
        plan = sub.plan
        sub.status = Subscription.ACTIVE
        if period_start or period_end or not already_recorded:
            sub.current_period_start = period_start or timezone.now()
            sub.current_period_end = period_end or default_period_end(sub.current_period_start, plan)
        sub.save(update_fields=['status', 'current_period_start', 'current_period_end', 'plan',
                                'gateway_plan_id', 'scheduled_plan', 'scheduled_gateway_plan_id',
                                'updated_at'])

        payment = _capture_payment(payment_id, {
            'user': sub.user,
            'payment_type': Payment.PLAN,
            'razorpay_order_id': order_id or '',
            'amount': amount if amount is not None else plan.price,
            'currency': currency or plan.currency,
            'credits_granted': plan.credits_per_cycle,
            'description': f"{plan.tier} plan ({plan.get_billing_cycle_display().lower()})",
            'plan': plan,
        })
        _grant_once(sub.user, plan.credits_per_cycle,
                    transaction_type=CreditTransaction.TYPE_PLAN_GRANT,
                    reference_id=payment_id, note=f"{plan.tier} cycle")
        receipts.issue_receipt(payment)
        _supersede_other_subscriptions(sub)
        return payment


def _supersede_other_subscriptions(sub):
    """A user holds one current plan. Any other pending checkout for the same
    user is abandoned once this one activates."""
    (Subscription.objects.filter(user=sub.user, status=Subscription.PENDING)
     .exclude(pk=sub.pk).update(status=Subscription.EXPIRED, updated_at=timezone.now()))


def apply_refund(payment, refund_id, refund_amount):
    """Record a (possibly partial) refund and claw back the proportional credits.

    The clawback is capped at the current balance because credits already spent
    on delivered documents cannot be recovered; the Refund row keeps both the
    owed and the reversed figures so reconciliation can see any shortfall.
    Idempotent per gateway refund id. Returns the Refund (or None if ignored)."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('user').get(pk=payment.pk)
        existing = Refund.objects.filter(razorpay_refund_id=refund_id).first()
        if existing:
            return existing
        if payment.status not in (Payment.CAPTURED, Payment.PARTIALLY_REFUNDED):
            logger.warning("Refund %s ignored: payment %s is %s",
                           refund_id, payment.razorpay_payment_id, payment.status)
            return None

        already = payment.refunds.aggregate(s=Sum('amount'))['s'] or Decimal('0')
        refund_amount = min(Decimal(refund_amount), payment.amount - already)
        if refund_amount <= 0:
            return None

        owed = 0
        if payment.credits_granted > 0 and payment.amount > 0:
            share = Decimal(payment.credits_granted) * refund_amount / payment.amount
            owed = int(share.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        take = min(owed, wallet.balance(payment.user))
        if take > 0:
            note = f"Refund reversal for {payment.razorpay_payment_id}"
            if take < owed:
                note += f" (shortfall {owed - take}: credits already used)"
            wallet.debit(payment.user, take, transaction_type=CreditTransaction.TYPE_REFUND,
                         reference_id=refund_id, note=note[:255])
        if take < owed:
            logger.warning("Refund %s: clawed back %s of %s credits for user %s",
                           refund_id, take, owed, payment.user_id)

        refund = Refund.objects.create(payment=payment, razorpay_refund_id=refund_id,
                                       amount=refund_amount, credits_owed=owed,
                                       credits_reversed=take)
        payment.status = (Payment.REFUNDED if already + refund_amount >= payment.amount
                          else Payment.PARTIALLY_REFUNDED)
        payment.save(update_fields=['status'])
        return refund
