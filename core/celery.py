import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('core')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

from celery.schedules import crontab

# Periodic tasks (require a running Celery Beat scheduler):
#   - promote due scheduled campaigns to the send queue every minute
#   - recover recipients abandoned mid-send by a dead worker every 10 minutes
app.conf.beat_schedule = {
    'trigger-scheduled-campaigns': {
        'task': 'emails.tasks.trigger_scheduled_campaigns',
        'schedule': crontab(minute='*'),
    },
    'reap-stale-sending-recipients': {
        'task': 'emails.tasks.reap_stale_sending_recipients_task',
        'schedule': crontab(minute='*/10'),
    },
    'notify-upcoming-scheduled-campaigns': {
        'task': 'emails.tasks.notify_upcoming_scheduled_campaigns',
        'schedule': crontab(minute=0),  # hourly: reminder when a run enters the freeze window
    },
    'purge-soft-deleted-records': {
        'task': 'core.tasks.purge_soft_deleted_records',
        'schedule': crontab(hour=3, minute=0, day_of_week=0),  # weekly, Sunday 03:00
    },
    # Expire plans past their paid period (lost-webhook safety net, and the end
    # path for manual plans) and abandon stale unpaid checkouts.
    'billing-expire-lapsed-subscriptions': {
        'task': 'billing.tasks.expire_lapsed_subscriptions_task',
        'schedule': crontab(minute=15),  # hourly
    },
    # Ops console insights: snapshot host/app metrics and evaluate thresholds.
    'opsconsole-collect-metrics': {
        'task': 'opsconsole.tasks.collect_metrics',
        'schedule': crontab(minute='*/5'),
    },
    # Trim old operational telemetry (audit/alert tables are never pruned here).
    'opsconsole-prune-snapshots': {
        'task': 'opsconsole.tasks.prune_metric_snapshots',
        'schedule': crontab(hour=4, minute=0, day_of_week=0),  # weekly, Sunday 04:00
    },
}

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
