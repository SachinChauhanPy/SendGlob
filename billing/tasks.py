"""Periodic billing housekeeping (scheduled in core/celery.py)."""
import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from . import conf
from .models import PackOrder, Subscription

logger = logging.getLogger(__name__)


def expire_lapsed_subscriptions(now=None):
    """End plans whose paid period is over, and abandon stale checkouts.

    - ACTIVE / PAST_DUE subscriptions past ``current_period_end`` plus the grace
      window are expired. Gateway plans normally end via webhook; this is the
      safety net for a lost webhook and the only end path for manual plans.
    - PENDING subscriptions and unpaid pack orders older than the checkout TTL
      are expired so they stop cluttering the UI and reconciliation.

    Returns a dict of counts for logging and tests."""
    now = now or timezone.now()
    lapsed_before = now - timedelta(days=conf.subscription_grace_days())
    stale_before = now - timedelta(hours=conf.pending_checkout_ttl_hours())

    lapsed = (Subscription.objects.current()
              .filter(current_period_end__isnull=False, current_period_end__lt=lapsed_before)
              .update(status=Subscription.EXPIRED, updated_at=now))
    abandoned_subs = (Subscription.objects
                      .filter(status=Subscription.PENDING, created_at__lt=stale_before)
                      .update(status=Subscription.EXPIRED, updated_at=now))
    abandoned_orders = (PackOrder.objects
                        .filter(Q(status=PackOrder.CREATED) | Q(status=PackOrder.FAILED),
                                created_at__lt=stale_before)
                        .update(status=PackOrder.EXPIRED))
    result = {'lapsed': lapsed, 'abandoned_subscriptions': abandoned_subs,
              'abandoned_orders': abandoned_orders}
    if any(result.values()):
        logger.info('Billing housekeeping: %s', result)
    return result


@shared_task
def expire_lapsed_subscriptions_task():
    return expire_lapsed_subscriptions()
