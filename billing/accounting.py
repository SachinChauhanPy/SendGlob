"""Revenue rollups and reconciliation.

All figures derive from immutable Payment / CreditTransaction / Receipt rows, so
reports are reproducible and auditable. The operator's core question is "did this
month's revenue cover the server cost"; ``cost_floor_net`` answers it directly.
"""
from collections import defaultdict
from datetime import date

from django.db.models import F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import conf
from .models import CreditTransaction, CreditWallet, PackOrder, Payment, Refund, WebhookEvent

# Payments where money was received (a refund does not erase the original sale).
SETTLED_STATUSES = [Payment.CAPTURED, Payment.PARTIALLY_REFUNDED, Payment.REFUNDED]


def _month_bounds(year, month):
    start = timezone.make_aware(timezone.datetime(year, month, 1))
    if month == 12:
        end = timezone.make_aware(timezone.datetime(year + 1, 1, 1))
    else:
        end = timezone.make_aware(timezone.datetime(year, month + 1, 1))
    return start, end


def monthly_revenue(year, month):
    """Net revenue for a month, split by currency and by pack vs plan.

    Gross is every settled payment (captured, even if later refunded) paid in the
    month; refunds are subtracted in the month they were issued, matching how
    the money actually moved."""
    start, end = _month_bounds(year, month)
    settled = Payment.objects.filter(status__in=SETTLED_STATUSES,
                                     paid_at__gte=start, paid_at__lt=end)
    by_currency = defaultdict(float)
    by_type = defaultdict(float)
    for row in settled.values('currency', 'payment_type').annotate(total=Sum('amount')):
        by_currency[row['currency']] += float(row['total'] or 0)
        by_type[row['payment_type']] += float(row['total'] or 0)
    refunds = (Refund.objects.filter(created_at__gte=start, created_at__lt=end)
               .values('payment__currency', 'payment__payment_type').annotate(total=Sum('amount')))
    refunded = 0.0
    for row in refunds:
        amount = float(row['total'] or 0)
        by_currency[row['payment__currency']] -= amount
        by_type[row['payment__payment_type']] -= amount
        refunded += amount
    return {
        'year': year, 'month': month,
        'by_currency': dict(by_currency),
        'by_type': dict(by_type),
        'refunded': refunded,
        'paying_users': settled.values('user').distinct().count(),
        'payment_count': settled.count(),
    }


def revenue_summary(months=6):
    """Trailing-N-month rollup, newest first."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        out.append(monthly_revenue(y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def cost_floor_net(year=None, month=None):
    """Net of this month's revenue against the configured infra cost floor.

    Sums all currencies naively (the operator typically has one settlement
    currency); returns the floor, total, net and an above/below verdict."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    rev = monthly_revenue(year, month)
    total = sum(rev['by_currency'].values())
    floor = conf.infra_cost_floor()
    net = total - floor
    return {
        'year': year, 'month': month, 'revenue': total, 'floor': floor,
        'net': net, 'above_floor': net >= 0,
    }


def reconcile():
    """Cross-check the money trail and return a list of discrepancy strings.

    Flags:
    - settled payments with no receipt;
    - refunded payments with no Refund record, and refunds whose credit
      reversal is missing from the ledger;
    - pack orders marked paid without a payment;
    - processed webhook captures that produced no local payment record;
    - wallet balance discrepancies against the append-only ledger;
    - webhook events stored but never processed.

    A refund that reversed fewer credits than owed (the user had spent them) is
    expected and recorded on the Refund row, so it is not reported here.
    """
    issues = []
    for pay_id in (Payment.objects.filter(status__in=SETTLED_STATUSES, receipt__isnull=True)
                   .values_list('razorpay_payment_id', flat=True)):
        issues.append(f"Captured payment {pay_id} has no receipt.")
    for pay_id in (Payment.objects
                   .filter(status__in=[Payment.REFUNDED, Payment.PARTIALLY_REFUNDED],
                           refunds__isnull=True)
                   .values_list('razorpay_payment_id', flat=True)):
        issues.append(f"Refunded payment {pay_id} has no refund record.")
    for refund in Refund.objects.filter(credits_reversed__gt=0).select_related('payment'):
        if not CreditTransaction.objects.filter(
                user_id=refund.payment.user_id, reference_id=refund.razorpay_refund_id,
                transaction_type=CreditTransaction.TYPE_REFUND).exists():
            issues.append(f"Refund {refund.razorpay_refund_id} has no ledger reversal.")
    for ref in (PackOrder.objects.filter(status=PackOrder.PAID, payment__isnull=True)
                .values_list('reference', flat=True)):
        issues.append(f"Pack order {ref} is marked paid but has no payment.")
    known = set(Payment.objects.values_list('razorpay_payment_id', flat=True))
    for ev in WebhookEvent.objects.filter(event_type='payment.captured', processed_at__isnull=False):
        pay_id = ((ev.payload or {}).get('payment') or {}).get('entity', {}).get('id')
        if pay_id and pay_id not in known:
            issues.append(f"Webhook {ev.event_id} captured payment {pay_id} has no local Payment record.")
    ledger = (CreditTransaction.objects.filter(user_id=OuterRef('user_id'))
              .values('user_id').annotate(s=Sum('amount')).values('s'))
    mismatched = (CreditWallet.objects
                  .annotate(ledger_sum=Coalesce(Subquery(ledger), Value(0)))
                  .exclude(balance=F('ledger_sum')))
    for w in mismatched:
        issues.append(f"User {w.user_id} wallet balance mismatch: wallet={w.balance}, "
                      f"ledger sum={w.ledger_sum}.")
    unprocessed = WebhookEvent.objects.filter(processed_at__isnull=True).count()
    if unprocessed:
        issues.append(f"{unprocessed} webhook event(s) stored but never processed.")
    return issues
