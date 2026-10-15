"""Live, read-only health probes plus host/EC2 metric collection.

Phase A reads what the system already stores or can cheaply probe (TaskResult,
campaign/recipient/document counts, Celery/Redis liveness). Phase B adds host
metrics via psutil, EC2 instance detection via IMDS, optional CloudWatch CPU
credits, and sample assembly for the Beat collector. Every probe degrades
gracefully: an unreachable worker, broker, or metadata service yields a safe
default rather than raising, so the dashboard always renders.
"""
from datetime import timedelta

from django.utils import timezone


def _celery_app():
    from core.celery import app
    return app


def broker_status():
    """Ping the Redis broker. Returns ``{'ok': bool, 'detail': str}``."""
    try:
        from kombu import Connection
        from django.conf import settings
        with Connection(settings.CELERY_BROKER_URL) as conn:
            conn.ensure_connection(max_retries=1, timeout=2)
        return {'ok': True, 'detail': 'reachable'}
    except Exception as exc:  # noqa: BLE001 - liveness probe must never raise
        return {'ok': False, 'detail': str(exc)[:200]}


def worker_status():
    """Inspect active Celery workers. Cheap, time-boxed, never raises."""
    try:
        inspector = _celery_app().control.inspect(timeout=2)
        pong = inspector.ping() or {}
        workers = sorted(pong.keys())
        return {'ok': bool(workers), 'workers': workers, 'count': len(workers)}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'workers': [], 'count': 0, 'detail': str(exc)[:200]}


def task_health(window_hours=24):
    """Success/failure breakdown from ``TaskResult`` over a recent window."""
    from django.db.models import Count
    from django_celery_results.models import TaskResult

    since = timezone.now() - timedelta(hours=window_hours)
    qs = TaskResult.objects.filter(date_done__gte=since)
    total = qs.count()
    by_status = dict(
        qs.values_list('status').annotate(n=Count('id')).values_list('status', 'n')
    ) if total else {}
    failures = by_status.get('FAILURE', 0)
    failure_rate = round((failures / total) * 100, 1) if total else 0.0
    recent_failures = list(
        qs.filter(status='FAILURE')
        .order_by('-date_done')
        .values('task_name', 'date_done', 'traceback')[:10]
    )
    return {
        'window_hours': window_hours,
        'total': total,
        'by_status': by_status,
        'failures': failures,
        'failure_rate': failure_rate,
        'recent_failures': recent_failures,
    }


def activity_counts():
    """Today's headline counters for the dashboard tiles."""
    from django.db.models import Count

    from emails.models import EmailCampaign, Recipient
    from documents.models import DocumentTemplate

    User = _user_model()
    start_of_today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # ``objects`` (unfiltered) is intentional: operators must see counts that
    # include soft-deleted records too, per the manager rules.
    recipients_today = Recipient.objects.filter(sent_at__gte=start_of_today)
    sent_today = recipients_today.filter(status='sent').count()
    failed_today = recipients_today.filter(status='failed').count()

    docs_today = Recipient.objects.filter(
        document_file__gt='', updated_at__gte=start_of_today,
    ).count()

    signups_today = User.objects.filter(date_joined__gte=start_of_today).count()

    campaign_states = dict(
        EmailCampaign.objects.values_list('status')
        .annotate(n=Count('id'))
        .values_list('status', 'n')
    )

    return {
        'emails_sent_today': sent_today,
        'emails_failed_today': failed_today,
        'documents_today': docs_today,
        'signups_today': signups_today,
        'campaign_states': campaign_states,
        'active_campaigns': (
            campaign_states.get('queued', 0)
            + campaign_states.get('sending', 0)
            + campaign_states.get('scheduled', 0)
        ),
        'total_users': User.objects.count(),
        'total_templates': DocumentTemplate.objects.count(),
    }


def _user_model():
    from django.contrib.auth import get_user_model
    return get_user_model()


# --- Host metrics (psutil) ----------------------------------------------------
# The collector runs in-process on the EC2 instance, so psutil reads the live
# host. Every probe degrades to None rather than breaking collection.
def host_metrics():
    """Sampled host resource usage. Returns a dict of metric_name -> float|None."""
    import psutil

    out = {}
    try:
        out['host_cpu_percent'] = float(psutil.cpu_percent(interval=0.3))
    except Exception:  # noqa: BLE001
        out['host_cpu_percent'] = None

    try:
        vm = psutil.virtual_memory()
        out['host_ram_percent'] = float(vm.percent)
        out['host_ram_available_mb'] = round(vm.available / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        out['host_ram_percent'] = None
        out['host_ram_available_mb'] = None

    try:
        sw = psutil.swap_memory()
        out['host_swap_percent'] = float(sw.percent)
    except Exception:  # noqa: BLE001
        out['host_swap_percent'] = None

    try:
        load1 = psutil.getloadavg()[0]
        ncpu = psutil.cpu_count() or 1
        out['host_load_percent'] = round((load1 / ncpu) * 100, 1)
    except Exception:  # noqa: BLE001
        out['host_load_percent'] = None

    return out


def disk_metrics():
    """Root-volume usage plus a breakdown of the DB and media subtrees."""
    import os

    import psutil
    from django.conf import settings

    from . import config

    out = {}
    try:
        usage = psutil.disk_usage('/')
        out['disk_percent'] = float(usage.percent)
        out['disk_used_gb'] = round(usage.used / (1024 ** 3), 2)
        out['disk_total_gb'] = round(usage.total / (1024 ** 3), 2)
    except Exception:  # noqa: BLE001
        out['disk_percent'] = None
        out['disk_used_gb'] = None
        out['disk_total_gb'] = None

    try:
        db_path = settings.DATABASES['default']['NAME']
        out['db_bytes'] = float(os.path.getsize(db_path)) if os.path.exists(db_path) else 0.0
    except Exception:  # noqa: BLE001
        out['db_bytes'] = None

    media_root = getattr(settings, 'MEDIA_ROOT', None)
    subtree_bytes = {}
    if media_root:
        for sub in config.media_subtrees():
            subtree_bytes[sub] = _dir_size(os.path.join(str(media_root), sub))
    out['media_subtree_bytes'] = subtree_bytes
    out['media_total_bytes'] = float(sum(subtree_bytes.values())) if subtree_bytes else 0.0
    return out


def _dir_size(path):
    import os

    total = 0
    if not os.path.isdir(path):
        return 0.0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return float(total)


def queue_depth():
    """Approximate broker backlog: length of the default Celery queue list in
    Redis. Returns None if the broker is not Redis or is unreachable."""
    try:
        import redis
        from django.conf import settings

        url = settings.CELERY_BROKER_URL
        if not url.startswith('redis'):
            return None
        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        return float(client.llen('celery'))
    except Exception:  # noqa: BLE001
        return None


# --- EC2 instance identity (IMDS) --------------------------------------------
_IMDS_CACHE = {'resolved': False, 'instance_type': None, 'region': None}


def instance_identity():
    """Return ``{'instance_type', 'region', 'source'}``.

    Resolution order: settings override -> IMDSv2 -> cached None. Never raises
    and never blocks longer than ~1s; absence degrades gracefully.
    """
    from . import config

    override = config.instance_type_override()
    if override:
        return {'instance_type': override, 'region': None, 'source': 'settings'}

    if _IMDS_CACHE['resolved']:
        return {
            'instance_type': _IMDS_CACHE['instance_type'],
            'region': _IMDS_CACHE['region'],
            'source': 'imds',
        }

    instance_type, region = _probe_imds()
    _IMDS_CACHE.update(resolved=True, instance_type=instance_type, region=region)
    return {'instance_type': instance_type, 'region': region, 'source': 'imds'}


def _probe_imds():
    """Best-effort IMDSv2 read of instance-type and region. Returns (type, region)
    or (None, None) when unavailable (dev, no IAM, IMDS disabled)."""
    try:
        import requests

        base = 'http://169.254.169.254/latest'
        token = requests.put(
            f'{base}/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'},
            timeout=1,
        ).text
        headers = {'X-aws-ec2-metadata-token': token}
        instance_type = requests.get(
            f'{base}/meta-data/instance-type', headers=headers, timeout=1,
        ).text.strip()
        region = requests.get(
            f'{base}/meta-data/placement/region', headers=headers, timeout=1,
        ).text.strip()
        return (instance_type or None, region or None)
    except Exception:  # noqa: BLE001
        return (None, None)


def cpu_credit_balance():
    """Optional burstable CPU-credit balance via CloudWatch (boto3).

    Returns a float or None. Absence (no boto3, no IAM, non-burstable type) is
    expected and handled silently.
    """
    try:
        from datetime import timedelta as _td

        import boto3  # optional dependency, not in pyproject

        identity = instance_identity()
        itype = identity['instance_type']
        if not itype or not itype.startswith(('t2', 't3', 't4')):
            return None
        instance_id = _imds_instance_id()
        if not instance_id:
            return None
        cw = boto3.client('cloudwatch', region_name=identity['region'])
        end = timezone.now()
        resp = cw.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName='CPUCreditBalance',
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            StartTime=end - _td(minutes=15),
            EndTime=end,
            Period=300,
            Statistics=['Average'],
        )
        points = sorted(resp.get('Datapoints', []), key=lambda d: d['Timestamp'])
        return float(points[-1]['Average']) if points else None
    except Exception:  # noqa: BLE001
        return None


def _imds_instance_id():
    try:
        import requests

        base = 'http://169.254.169.254/latest'
        token = requests.put(
            f'{base}/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'},
            timeout=1,
        ).text
        return requests.get(
            f'{base}/meta-data/instance-id',
            headers={'X-aws-ec2-metadata-token': token}, timeout=1,
        ).text.strip() or None
    except Exception:  # noqa: BLE001
        return None


# --- Sample assembly ----------------------------------------------------------
SNAPSHOT_METRICS = (
    'host_cpu_percent', 'host_ram_percent', 'host_swap_percent', 'host_load_percent',
    'host_ram_available_mb', 'disk_percent', 'disk_used_gb', 'disk_total_gb',
    'db_bytes', 'media_total_bytes', 'queue_depth', 'task_failure_rate',
    'cpu_credit_balance',
)


def collect_samples():
    """Assemble the flat ``{metric: float|None}`` dict the collector snapshots
    and the insights engine evaluates. Runs the live probes once."""
    samples = {}
    samples.update({k: v for k, v in host_metrics().items()})
    disk = disk_metrics()
    samples.update({k: v for k, v in disk.items() if k in SNAPSHOT_METRICS})
    samples['queue_depth'] = queue_depth()
    samples['task_failure_rate'] = task_health(window_hours=24)['failure_rate']
    samples['cpu_credit_balance'] = cpu_credit_balance()
    samples['_media_subtree_bytes'] = disk.get('media_subtree_bytes', {})
    return samples


def latest_snapshots(metrics_list=None):
    """Most recent stored value per metric, for fast view rendering without
    re-probing the host. Returns ``{metric: {'value', 'captured_at'}}``."""
    from .models import MetricSnapshot

    names = metrics_list or SNAPSHOT_METRICS
    out = {}
    for name in names:
        row = (
            MetricSnapshot.objects.filter(metric=name)
            .order_by('-captured_at')
            .values('value', 'captured_at')
            .first()
        )
        if row:
            out[name] = row
    return out


def snapshot_series(metric, hours=24, max_points=288):
    """Time series for one metric, oldest-first, for trend charts."""
    from .models import MetricSnapshot

    since = timezone.now() - timedelta(hours=hours)
    rows = list(
        MetricSnapshot.objects.filter(metric=metric, captured_at__gte=since)
        .order_by('captured_at')
        .values_list('captured_at', 'value')
    )
    if len(rows) > max_points:
        step = len(rows) // max_points + 1
        rows = rows[::step]
    return [{'t': ts.isoformat(), 'v': v} for ts, v in rows]
