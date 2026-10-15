"""Rule-based, EC2-specific scaling insights.

Three questions per resource, per the plan: is it a problem now (threshold
evaluation), when will it become one (linear growth projection -> lead time),
and what exactly to change (sizing recommendations naming a concrete target
instance type or EBS size, or advising a service be moved off the box).

Intentionally rule-based and advisory only: nothing here resizes anything. An AI
summary layer can later read the same MetricSnapshot history without any change
to this module.
"""
from datetime import timedelta

from django.utils import timezone

from . import config


# --- 1. Is it a problem now? --------------------------------------------------
def evaluate_thresholds(samples):
    """Compare a flat ``{metric: value}`` dict against configured thresholds.

    Returns a list of breach dicts (worst level per metric). Metrics in
    ``LOWER_IS_WORSE`` (CPU credit balance) breach when the value falls *below*
    the threshold; all others when it rises above.
    """
    rules = config.thresholds()
    breaches = []
    for metric, value in samples.items():
        if value is None or metric not in rules:
            continue
        rule = rules[metric]
        lower_is_worse = metric in config.LOWER_IS_WORSE
        level = _breach_level(value, rule, lower_is_worse)
        if level:
            breaches.append({
                'metric': metric,
                'level': level,
                'value': float(value),
                'threshold': float(rule[level]),
                'message': _breach_message(metric, value, rule[level], level, lower_is_worse),
            })
    return breaches


def _breach_level(value, rule, lower_is_worse):
    if lower_is_worse:
        if value <= rule['critical']:
            return 'critical'
        if value <= rule['warn']:
            return 'warn'
        return None
    if value >= rule['critical']:
        return 'critical'
    if value >= rule['warn']:
        return 'warn'
    return None


def _breach_message(metric, value, threshold, level, lower_is_worse):
    direction = 'below' if lower_is_worse else 'above'
    pretty = metric.replace('host_', '').replace('_', ' ')
    return f"{pretty} is {value:.1f} ({level}, {direction} {threshold:.0f})"


# --- 2. When will it become one? ---------------------------------------------
def project_metric(metric, target, window_days=7, min_points=4):
    """Linear projection of ``metric`` toward ``target``.

    Fits ``value = slope * day + intercept`` over the recent window and returns
    the number of days until the line crosses ``target``. Returns ``None`` when
    there is insufficient history or the trend is flat/moving away (so we never
    invent a deadline from noise).
    """
    from .models import MetricSnapshot

    since = timezone.now() - timedelta(days=window_days)
    rows = list(
        MetricSnapshot.objects.filter(metric=metric, captured_at__gte=since)
        .order_by('captured_at')
        .values_list('captured_at', 'value')
    )
    if len(rows) < min_points:
        return None

    t0 = rows[0][0]
    xs = [(ts - t0).total_seconds() / 86400.0 for ts, _ in rows]
    ys = [v for _, v in rows]
    slope, intercept = _least_squares(xs, ys)
    if slope == 0:
        return None

    current_x = xs[-1]
    current_y = slope * current_x + intercept
    # Must be moving toward the target, not away from it.
    if target > current_y and slope <= 0:
        return None
    if target < current_y and slope >= 0:
        return None

    cross_x = (target - intercept) / slope
    days = cross_x - current_x
    return round(days, 1) if days >= 0 else None


def _least_squares(xs, ys):
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, (sy / n if n else 0.0)
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


# --- 3. What exactly should I change? ----------------------------------------
def sizing_recommendations(samples, identity=None):
    """Translate current pressure into concrete, EC2-specific levers.

    Each recommendation names the lever (resize instance / grow EBS / move a
    service) and, where the instance type is known, a concrete target. Disk is
    grown without an instance change; CPU/RAM pressure resizes the instance;
    a single service dominating a resource is advised off the box.
    """
    if identity is None:
        from . import metrics
        identity = metrics.instance_identity()

    instance_type = identity.get('instance_type')
    table = config.instance_types()
    spec = table.get(instance_type) if instance_type else None
    recs = []

    cpu = samples.get('host_cpu_percent')
    load = samples.get('host_load_percent')
    ram = samples.get('host_ram_percent')
    swap = samples.get('host_swap_percent')
    disk_pct = samples.get('disk_percent')
    disk_total = samples.get('disk_total_gb')
    credit = samples.get('cpu_credit_balance')
    th = config.thresholds()

    # CPU credit drain is the real story on burstable types even at moderate CPU%.
    if credit is not None and spec and spec.get('burstable') and credit <= th['cpu_credit_balance']['warn']:
        recs.append({
            'resource': 'CPU credits',
            'lever': 'instance',
            'severity': 'critical' if credit <= th['cpu_credit_balance']['critical'] else 'warn',
            'detail': (
                f"CPU credit balance is low ({credit:.0f}) on burstable "
                f"{instance_type}. Raw CPU% can look fine while credits drain. "
                "Enable unlimited mode, move up a burstable size, or switch to a "
                "non-burstable class."
            ),
            'target': _next_type(spec, table),
        })

    # CPU / load pressure -> resize instance (compute).
    cpu_pressure = _max_present(cpu, load)
    if cpu_pressure is not None and cpu_pressure >= th['host_cpu_percent']['warn']:
        ram_ok = ram is None or ram < th['host_ram_percent']['warn']
        if ram_ok and spec and not spec.get('burstable', False):
            target = _family_step(spec, table, 'c5')
        else:
            target = _next_type(spec, table)
        recs.append({
            'resource': 'CPU',
            'lever': 'instance',
            'severity': 'critical' if cpu_pressure >= th['host_cpu_percent']['critical'] else 'warn',
            'detail': (
                f"Sustained CPU pressure ({cpu_pressure:.0f}%). Pillow rendering and "
                "email batches are the main consumers. Resize the instance"
                + (f" to {target}" if target else " to the next compute step")
                + " (stop, change type, start; brief downtime)."
            ),
            'target': target,
        })

    # RAM / swap pressure -> resize instance (memory). Swap is near-critical.
    swapping = swap is not None and swap >= th['host_swap_percent']['warn']
    ram_pressure = ram is not None and ram >= th['host_ram_percent']['warn']
    if ram_pressure or swapping:
        cpu_idle = cpu_pressure is None or cpu_pressure < th['host_cpu_percent']['warn']
        if cpu_idle and spec:
            target = _family_step(spec, table, 'r5')
        else:
            target = _next_type(spec, table)
        recs.append({
            'resource': 'RAM',
            'lever': 'instance',
            'severity': 'critical' if swapping or (ram and ram >= th['host_ram_percent']['critical']) else 'warn',
            'detail': (
                ("Swap activity detected (degrades latency badly). " if swapping else "")
                + (f"Memory at {ram:.0f}%. " if ram is not None else "")
                + "Concurrent workers and image compositing drive memory. Resize"
                + (f" to {target}" if target else " to the next memory step") + "."
            ),
            'target': target,
        })

    # Disk pressure -> grow EBS online, name the target GB (no instance change).
    if disk_pct is not None and disk_pct >= th['disk_percent']['warn']:
        target_gb = _ebs_target_gb(disk_total)
        recs.append({
            'resource': 'Disk',
            'lever': 'ebs',
            'severity': 'critical' if disk_pct >= th['disk_percent']['critical'] else 'warn',
            'detail': (
                f"Disk at {disk_pct:.0f}%. Grow the EBS volume"
                + (f" from {disk_total:.0f} GB to {target_gb} GB" if disk_total and target_gb else "")
                + " (online resize + filesystem extend, no downtime). If media is "
                "the driver, also tune retention/purge; if db.sqlite3 is the driver, "
                "plan the Postgres move."
            ),
            'target': f"{target_gb} GB" if target_gb else None,
        })

    # Queue backlog -> add a worker, then move workers off the box at scale.
    qd = samples.get('queue_depth')
    if qd is not None and qd >= th['queue_depth']['warn']:
        recs.append({
            'resource': 'Queue',
            'lever': 'workers',
            'severity': 'critical' if qd >= th['queue_depth']['critical'] else 'warn',
            'detail': (
                f"Queue backlog is {qd:.0f}. Add a Celery worker; at higher scale "
                "move workers to a separate instance so they stop competing with the "
                "web process for CPU/RAM."
            ),
            'target': None,
        })

    return recs


def _next_type(spec, table):
    if not spec:
        return None
    nxt = spec.get('next')
    return nxt if nxt in table or nxt else nxt


def _family_step(spec, table, preferred_family):
    """Pick a same-or-greater capacity type in a more appropriate family.

    Used to steer a CPU-bound box toward compute-optimized (c5) or a RAM-bound
    box toward memory-optimized (r5) rather than just the next burstable size.
    """
    if not spec:
        return None
    want_vcpu = spec['vcpu']
    candidates = [
        (name, s) for name, s in table.items()
        if s['family'] == preferred_family and s['vcpu'] >= want_vcpu
    ]
    if not candidates:
        return spec.get('next')
    candidates.sort(key=lambda kv: (kv[1]['vcpu'], kv[1]['ram_gb']))
    return candidates[0][0]


def _ebs_target_gb(current_gb):
    """Recommend a sensible larger EBS size: next step that roughly doubles
    headroom, rounded to a tidy number."""
    if not current_gb:
        return None
    for step in (20, 30, 50, 100, 200, 300, 500, 1000):
        if step > current_gb * 1.4:
            return step
    return int(current_gb * 2)


def _max_present(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None
