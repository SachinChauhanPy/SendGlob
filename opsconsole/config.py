"""Tunable configuration for the operations console insights engine.

Every value here is a default that can be overridden in Django settings by
defining a setting of the same name (read via ``getattr``). This keeps the
thresholds, snapshot interval and the EC2 instance reference table tunable
without code changes, exactly as the plan requires, while not forcing an edit to
``core/settings.py``. To override, add e.g. ``OPS_THRESHOLDS = {...}`` to
settings; anything not overridden falls back to the defaults below.

Available overrides:
    OPS_METRIC_INTERVAL_MINUTES   - Beat collection cadence (default 5).
    OPS_METRIC_RETENTION_DAYS     - how long MetricSnapshot rows are kept (90).
    OPS_OPERATOR_ALERT_EMAIL      - recipient for critical alert emails.
    OPS_INSTANCE_TYPE             - override IMDS detection (e.g. 't3.small').
    OPS_EBS_VOLUME_GB             - attached EBS size in GB (for disk advice).
    OPS_THRESHOLDS                - {metric: {'warn': x, 'critical': y}}.
    OPS_INSTANCE_TYPES            - {type: {'vcpu', 'ram_gb', 'family', 'next'}}.
    OPS_MEDIA_SUBTREES            - media subdirs to size individually.
"""
from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


# --- Collection ---------------------------------------------------------------
def metric_interval_minutes():
    return int(_get('OPS_METRIC_INTERVAL_MINUTES', 5))


def metric_retention_days():
    return int(_get('OPS_METRIC_RETENTION_DAYS', 90))


def operator_alert_email():
    """Where critical-alert emails go. Falls back to the first ADMINS entry, then
    DEFAULT_FROM_EMAIL, so an alert always has somewhere to land."""
    explicit = _get('OPS_OPERATOR_ALERT_EMAIL', None)
    if explicit:
        return explicit
    admins = _get('ADMINS', [])
    if admins:
        # ADMINS entries are (name, email) pairs.
        return admins[0][1]
    return _get('DEFAULT_FROM_EMAIL', None)


# --- Host / EC2 ---------------------------------------------------------------
def instance_type_override():
    return _get('OPS_INSTANCE_TYPE', None)


def ebs_volume_gb():
    return _get('OPS_EBS_VOLUME_GB', None)


def media_subtrees():
    return _get('OPS_MEDIA_SUBTREES', ['signatures', 'generated_documents', 'document_templates'])


# --- Thresholds ---------------------------------------------------------------
# Percentages unless noted. A metric crossing ``warn`` raises a warn alert,
# ``critical`` a critical alert (which also emails the operator). Conservative
# starting points; tune from real data (see plan open questions).
_DEFAULT_THRESHOLDS = {
    'host_cpu_percent': {'warn': 80.0, 'critical': 92.0},
    'host_ram_percent': {'warn': 80.0, 'critical': 90.0},
    'host_swap_percent': {'warn': 1.0, 'critical': 20.0},   # any swap is bad
    'disk_percent': {'warn': 80.0, 'critical': 90.0},
    'queue_depth': {'warn': 200.0, 'critical': 1000.0},
    'task_failure_rate': {'warn': 10.0, 'critical': 25.0},
    'cpu_credit_balance': {'warn': 30.0, 'critical': 10.0},  # lower is worse
}

# Metrics where a LOW value is the problem (credit balance), so comparison flips.
LOWER_IS_WORSE = {'cpu_credit_balance'}


def thresholds():
    merged = {k: dict(v) for k, v in _DEFAULT_THRESHOLDS.items()}
    merged.update(_get('OPS_THRESHOLDS', {}))
    return merged


# --- EC2 instance reference table --------------------------------------------
# vCPU / RAM(GB) / family, plus the recommended "next step up" within a family.
# Used to turn "85% RAM sustained on t3.small" into "recommend t3.medium (4 GB)".
_DEFAULT_INSTANCE_TYPES = {
    't3.micro':   {'vcpu': 2, 'ram_gb': 1,  'family': 't3', 'burstable': True,  'next': 't3.small'},
    't3.small':   {'vcpu': 2, 'ram_gb': 2,  'family': 't3', 'burstable': True,  'next': 't3.medium'},
    't3.medium':  {'vcpu': 2, 'ram_gb': 4,  'family': 't3', 'burstable': True,  'next': 't3.large'},
    't3.large':   {'vcpu': 2, 'ram_gb': 8,  'family': 't3', 'burstable': True,  'next': 't3.xlarge'},
    't3.xlarge':  {'vcpu': 4, 'ram_gb': 16, 'family': 't3', 'burstable': True,  'next': 't3.2xlarge'},
    't3.2xlarge': {'vcpu': 8, 'ram_gb': 32, 'family': 't3', 'burstable': True,  'next': 'm5.2xlarge'},
    'm5.large':   {'vcpu': 2, 'ram_gb': 8,  'family': 'm5', 'burstable': False, 'next': 'm5.xlarge'},
    'm5.xlarge':  {'vcpu': 4, 'ram_gb': 16, 'family': 'm5', 'burstable': False, 'next': 'm5.2xlarge'},
    'c5.large':   {'vcpu': 2, 'ram_gb': 4,  'family': 'c5', 'burstable': False, 'next': 'c5.xlarge'},
    'c5.xlarge':  {'vcpu': 4, 'ram_gb': 8,  'family': 'c5', 'burstable': False, 'next': 'c5.2xlarge'},
    'r5.large':   {'vcpu': 2, 'ram_gb': 16, 'family': 'r5', 'burstable': False, 'next': 'r5.xlarge'},
    'r5.xlarge':  {'vcpu': 4, 'ram_gb': 32, 'family': 'r5', 'burstable': False, 'next': 'r5.2xlarge'},
}


def instance_types():
    merged = dict(_DEFAULT_INSTANCE_TYPES)
    merged.update(_get('OPS_INSTANCE_TYPES', {}))
    return merged
