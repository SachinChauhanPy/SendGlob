"""Celery tasks for the operations console insights engine.

``collect_metrics`` (Beat, every few minutes) snapshots scalar metrics, evaluates
thresholds, raises de-duplicated alerts, and emails the operator on new critical
events. ``prune_metric_snapshots`` trims old telemetry on a long retention.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def collect_metrics():
    """Sample host + app metrics, persist them, and evaluate thresholds.

    Safe to run repeatedly. Alert creation is de-duplicated so a sustained breach
    raises one alert until acknowledged, rather than one per collection cycle.
    """
    from . import insights, metrics
    from .models import AlertEvent, MetricSnapshot

    samples = metrics.collect_samples()

    # Persist scalar metrics only (skip None and the nested breakdown key).
    rows = [
        MetricSnapshot(metric=name, value=float(value))
        for name, value in samples.items()
        if name in metrics.SNAPSHOT_METRICS and value is not None
    ]
    if rows:
        MetricSnapshot.objects.bulk_create(rows)

    breaches = insights.evaluate_thresholds(
        {k: v for k, v in samples.items() if not k.startswith('_')}
    )
    new_critical = []
    for breach in breaches:
        created = _raise_alert_if_new(AlertEvent, breach)
        if created and breach['level'] == AlertEvent.CRITICAL:
            new_critical.append(breach)

    if new_critical:
        _email_operator(new_critical)

    return {
        'snapshots': len(rows),
        'breaches': len(breaches),
        'new_critical': len(new_critical),
    }


def _raise_alert_if_new(AlertEvent, breach):
    """Create an AlertEvent unless an unacknowledged one already exists for the
    same metric+level. Returns True if a new row was created."""
    exists = AlertEvent.objects.filter(
        metric=breach['metric'],
        level=breach['level'],
        acknowledged_at__isnull=True,
    ).exists()
    if exists:
        return False
    AlertEvent.objects.create(
        metric=breach['metric'],
        level=breach['level'],
        message=breach['message'],
        value=breach['value'],
        threshold=breach['threshold'],
    )
    return True


def _email_operator(critical_breaches):
    """Email the operator about new critical alerts. Never raises into the task;
    a failed notification must not fail metric collection."""
    from django.conf import settings
    from django.core.mail import send_mail

    from . import config

    recipient = config.operator_alert_email()
    if not recipient:
        logger.warning('No operator alert email configured; skipping notification.')
        return

    lines = [f"- {b['message']}" for b in critical_breaches]
    body = (
        "New critical operational alert(s) on SendGlob:\n\n"
        + "\n".join(lines)
        + "\n\nOpen the ops console capacity view for recommendations.\n"
    )
    try:
        send_mail(
            subject=f"[SendGlob Ops] {len(critical_breaches)} critical alert(s)",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001
        logger.exception('Failed to send operator alert email.')


@shared_task
def prune_metric_snapshots():
    """Delete MetricSnapshot rows older than the configured retention. Audit and
    alert tables are never pruned here; only operational telemetry."""
    from . import config
    from .models import MetricSnapshot

    cutoff = timezone.now() - timedelta(days=config.metric_retention_days())
    deleted, _ = MetricSnapshot.objects.filter(captured_at__lt=cutoff).delete()
    return {'deleted': deleted}
