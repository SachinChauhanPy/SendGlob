"""Operations console views.

Phase A: read-only dashboard, system health, activity monitor, audit log.
Phase B: capacity planning (insights, trends, EC2 sizing recommendations) plus an
operator-gated, audited alert-acknowledge action. All views are gated by
:func:`opsconsole.access.staff_member_required`; the one write (acknowledge) is
additionally gated by :func:`operator_required` and writes an audit row.
"""
import json

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import activity, config, insights, metrics
from .access import client_ip, operator_required, staff_member_required
from .models import AdminAuditLog, AlertEvent


@staff_member_required
def dashboard(request):
    """At-a-glance system health landing tiles."""
    context = {
        'counts': metrics.activity_counts(),
        'workers': metrics.worker_status(),
        'broker': metrics.broker_status(),
        'tasks': metrics.task_health(window_hours=24),
        'feed': activity.recent_activity(limit=12),
        'active_alerts': AlertEvent.objects.filter(acknowledged_at__isnull=True).count(),
        'active_section': 'dashboard',
    }
    return render(request, 'opsconsole/dashboard.html', context)


@staff_member_required
def health(request):
    """Detailed worker / broker / task-failure view."""
    context = {
        'workers': metrics.worker_status(),
        'broker': metrics.broker_status(),
        'tasks_24h': metrics.task_health(window_hours=24),
        'tasks_1h': metrics.task_health(window_hours=1),
        'active_section': 'health',
    }
    return render(request, 'opsconsole/health.html', context)


@staff_member_required
def activity_monitor(request):
    """Unified, filterable activity feed."""
    kind = request.GET.get('kind', '').strip()
    feed = activity.recent_activity(limit=80)
    if kind:
        feed = [item for item in feed if item['kind'] == kind]
    context = {
        'feed': feed,
        'kind': kind,
        'kinds': ['campaign', 'signup', 'audit'],
        'active_section': 'activity',
    }
    return render(request, 'opsconsole/activity.html', context)


@staff_member_required
def audit(request):
    """Read-only view of the immutable operator audit log."""
    rows = AdminAuditLog.objects.select_related('actor')[:200]
    return render(request, 'opsconsole/audit.html', {'rows': rows, 'active_section': 'audit'})


# --- Phase B: capacity planning ----------------------------------------------
# Metrics rendered as trend charts and their human labels / units.
_TREND_METRICS = [
    ('host_cpu_percent', 'CPU %', '%'),
    ('host_ram_percent', 'RAM %', '%'),
    ('disk_percent', 'Disk %', '%'),
    ('queue_depth', 'Queue depth', ''),
]


@staff_member_required
def capacity(request):
    """EC2 capacity planning: current pressure, trends, projections, and concrete
    sizing recommendations. Reads the latest stored snapshots (fast) rather than
    re-probing the host on every page load."""
    latest = metrics.latest_snapshots()
    samples = {name: row['value'] for name, row in latest.items()}

    identity = metrics.instance_identity()
    spec = config.instance_types().get(identity.get('instance_type')) if identity.get('instance_type') else None

    recommendations = insights.sizing_recommendations(samples, identity)

    # Lead-time projections toward each metric's warn threshold.
    thresholds = config.thresholds()
    projections = []
    for name, label, _unit in _TREND_METRICS:
        rule = thresholds.get(name)
        if not rule:
            continue
        days = insights.project_metric(name, rule['warn'])
        if days is not None:
            projections.append({'metric': label, 'days': days, 'threshold': rule['warn']})

    charts = []
    for name, label, unit in _TREND_METRICS:
        charts.append({
            'id': name,
            'label': label,
            'unit': unit,
            'latest': samples.get(name),
            'series': metrics.snapshot_series(name, hours=24),
            'warn': thresholds.get(name, {}).get('warn'),
            'critical': thresholds.get(name, {}).get('critical'),
        })

    active_alerts = list(AlertEvent.objects.filter(acknowledged_at__isnull=True))

    context = {
        'identity': identity,
        'spec': spec,
        'samples': samples,
        'latest': latest,
        'recommendations': recommendations,
        'projections': projections,
        'charts_json': json.dumps(charts, default=str),
        'charts': charts,
        'active_alerts': active_alerts,
        'media_subtrees': samples.get('media_total_bytes'),
        'has_data': bool(latest),
        'active_section': 'capacity',
    }
    return render(request, 'opsconsole/capacity.html', context)


@operator_required
@require_POST
def acknowledge_alert(request, pk):
    """Acknowledge an active alert. Operator-gated and audit-logged. Clears the
    de-duplication suppression so a later breach can re-alert."""
    try:
        alert = AlertEvent.objects.get(pk=pk, acknowledged_at__isnull=True)
    except AlertEvent.DoesNotExist:
        messages.info(request, 'Alert already acknowledged or not found.')
        return redirect('opsconsole:capacity')

    alert.acknowledged_at = timezone.now()
    alert.acknowledged_by = request.user
    alert.save(update_fields=['acknowledged_at', 'acknowledged_by'])

    AdminAuditLog.record(
        actor=request.user,
        action='acknowledge_alert',
        target=alert,
        reason=request.POST.get('reason', ''),
        detail={'metric': alert.metric, 'level': alert.level, 'value': alert.value},
        ip_address=client_ip(request),
    )
    messages.success(request, f'Acknowledged {alert.level} alert for {alert.metric}.')
    return redirect('opsconsole:capacity')


# --- Phase C: control actions ------------------------------------------------
# Read helpers and operator-gated, audited mutations. Views stay thin: they parse
# the request, call an audited function in opsconsole.controls, surface the
# returned message, and redirect. All mutations require a typed reason.
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from . import controls, flags
from .access import operator_required
from .middleware import ImpersonationMiddleware

IMPERSONATION_WINDOW = timedelta(minutes=30)


@operator_required
def controls_home(request):
    """Global runtime controls: kill switch, maintenance mode, throttle override."""
    from .models import OperationalFlag
    existing = {f.key: f for f in OperationalFlag.objects.all()}
    context = {
        'send_paused': flags.is_send_paused(),
        'maintenance': flags.is_maintenance(),
        'throttle': flags.send_throttle_override(default=''),
        'flags': existing,
        'active_section': 'controls',
    }
    return render(request, 'opsconsole/controls.html', context)


@operator_required
@require_POST
def toggle_flag(request, key):
    """Toggle the kill switch or maintenance mode. Reason required."""
    valid = {flags.SEND_PAUSED, flags.MAINTENANCE}
    if key not in valid:
        messages.error(request, 'Unknown flag.')
        return redirect('opsconsole:controls')
    enabled = request.POST.get('enabled') == 'on'
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'A reason is required for this action.')
        return redirect('opsconsole:controls')
    ok, msg = controls.set_flag(actor=request.user, key=key, enabled=enabled,
                                reason=reason, ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:controls')


@operator_required
@require_POST
def set_throttle(request):
    seconds = request.POST.get('seconds', '').strip()
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'A reason is required for this action.')
        return redirect('opsconsole:controls')
    ok, msg = controls.set_throttle(actor=request.user, seconds=seconds or None,
                                    reason=reason, ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:controls')


@staff_member_required
def users_list(request):
    """Search users by email."""
    User = get_user_model()
    q = request.GET.get('q', '').strip()
    users = User.objects.all().order_by('-date_joined')
    if q:
        users = users.filter(email__icontains=q)
    context = {'users': users[:100], 'q': q, 'active_section': 'users'}
    return render(request, 'opsconsole/users.html', context)


@staff_member_required
def user_detail(request, pk):
    """User profile: wallet balance, recent ledger, campaigns."""
    from billing.models import get_or_create_wallet

    from emails.models import EmailCampaign

    User = get_user_model()
    user = get_object_or_404(User, pk=pk)
    wallet = get_or_create_wallet(user)
    context = {
        'target': user,
        'wallet': wallet,
        'ledger': user.credit_transactions.all()[:25],
        'campaigns': EmailCampaign.objects.filter(user=user).order_by('-created_at')[:25],
        'active_section': 'users',
    }
    return render(request, 'opsconsole/user_detail.html', context)


def _require_reason(request):
    reason = request.POST.get('reason', '').strip()
    return reason


@operator_required
@require_POST
def grant_credits(request, pk):
    User = get_user_model()
    user = get_object_or_404(User, pk=pk)
    reason = _require_reason(request)
    if not reason:
        messages.error(request, 'A reason is required for this action.')
        return redirect('opsconsole:user_detail', pk=pk)
    ok, msg = controls.grant_credits(actor=request.user, user=user,
                                     amount=request.POST.get('amount', '0'),
                                     reason=reason, ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:user_detail', pk=pk)


@operator_required
@require_POST
def set_user_active(request, pk):
    User = get_user_model()
    user = get_object_or_404(User, pk=pk)
    reason = _require_reason(request)
    if not reason:
        messages.error(request, 'A reason is required for this action.')
        return redirect('opsconsole:user_detail', pk=pk)
    active = request.POST.get('active') == 'on'
    ok, msg = controls.set_user_active(actor=request.user, user=user, active=active,
                                       reason=reason, ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:user_detail', pk=pk)


@operator_required
@require_POST
def force_logout(request, pk):
    User = get_user_model()
    user = get_object_or_404(User, pk=pk)
    ok, msg = controls.force_logout(actor=request.user, user=user,
                                    reason=_require_reason(request),
                                    ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:user_detail', pk=pk)


@operator_required
@require_POST
def impersonate_start(request, pk):
    """Begin a read-only impersonation session for the target user."""
    User = get_user_model()
    target = get_object_or_404(User, pk=pk)
    if target.is_staff:
        messages.error(request, 'Refusing to impersonate another staff user.')
        return redirect('opsconsole:user_detail', pk=pk)
    expires = (timezone.now() + IMPERSONATION_WINDOW).timestamp()
    request.session[ImpersonationMiddleware.SESSION_KEY] = {
        'target_id': target.pk,
        'operator_id': request.user.pk,
        'expires': expires,
    }
    AdminAuditLog.record(actor=request.user, action='impersonate_start', target=target,
                         reason=_require_reason(request), ip_address=client_ip(request))
    messages.info(request, f'Now viewing as {target.email}. This session is read-only.')
    return redirect('emails:dashboard')


@require_POST
def impersonate_stop(request):
    """End an impersonation session. Reachable while impersonating (request.user
    is the target), so it relies on request.impersonator rather than a staff gate."""
    operator = getattr(request, 'impersonator', None)
    target = getattr(request, 'user', None)
    request.session.pop(ImpersonationMiddleware.SESSION_KEY, None)
    if operator:
        AdminAuditLog.record(actor=operator, action='impersonate_stop', target=target,
                             ip_address=client_ip(request))
        messages.success(request, 'Stopped impersonating.')
        return redirect('opsconsole:user_detail', pk=target.pk)
    return redirect('opsconsole:dashboard')


@staff_member_required
def campaigns_list(request):
    """Find any campaign, including soft-deleted (operators use the unfiltered
    manager deliberately, per the manager rules)."""
    from emails.models import EmailCampaign

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    campaigns = EmailCampaign.objects.select_related('user').order_by('-created_at')
    if q:
        campaigns = campaigns.filter(name__icontains=q)
    if status:
        campaigns = campaigns.filter(status=status)
    context = {
        'campaigns': campaigns[:100], 'q': q, 'status': status,
        'statuses': [c[0] for c in EmailCampaign.STATUS_CHOICES],
        'active_section': 'campaigns',
    }
    return render(request, 'opsconsole/campaigns.html', context)


@staff_member_required
def campaign_detail(request, pk):
    from emails.models import EmailCampaign

    campaign = get_object_or_404(EmailCampaign.objects.select_related('user'), pk=pk)
    recipients = campaign.recipients.all()
    context = {
        'campaign': campaign,
        'recipient_total': recipients.count(),
        'sent': recipients.filter(status='sent').count(),
        'failed': recipients.filter(status='failed').count(),
        'pending': recipients.filter(status='pending').count(),
        'active_section': 'campaigns',
    }
    return render(request, 'opsconsole/campaign_detail.html', context)


@operator_required
@require_POST
def campaign_action(request, pk, action):
    """Cancel or requeue any campaign through the validated control functions."""
    from emails.models import EmailCampaign

    campaign = get_object_or_404(EmailCampaign.objects, pk=pk)
    reason = _require_reason(request)
    if not reason:
        messages.error(request, 'A reason is required for this action.')
        return redirect('opsconsole:campaign_detail', pk=pk)
    fn = {'cancel': controls.cancel_campaign, 'requeue': controls.requeue_campaign}.get(action)
    if not fn:
        messages.error(request, 'Unknown action.')
        return redirect('opsconsole:campaign_detail', pk=pk)
    ok, msg = fn(actor=request.user, campaign=campaign, reason=reason,
                 ip_address=client_ip(request))
    messages.success(request, msg) if ok else messages.error(request, msg)
    return redirect('opsconsole:campaign_detail', pk=pk)


# --- Contact messages (public pages plan s.4) ---

@staff_member_required
def messages_list(request):
    """Inbound contact-form messages with a status filter."""
    from pages.models import ContactMessage

    status = request.GET.get('status', '').strip()
    items = ContactMessage.objects.select_related('user', 'handled_by')
    if status in dict(ContactMessage.STATUS_CHOICES):
        items = items.filter(status=status)
    context = {
        'items': items[:200],
        'status': status,
        'status_choices': ContactMessage.STATUS_CHOICES,
        'active_section': 'messages',
    }
    return render(request, 'opsconsole/messages.html', context)


@operator_required
@require_POST
def message_set_status(request, pk):
    """Mark a contact message read/resolved (audited, backend-validated)."""
    from pages.models import ContactMessage

    msg = get_object_or_404(ContactMessage, pk=pk)
    new_status = request.POST.get('status', '')
    if new_status not in dict(ContactMessage.STATUS_CHOICES):
        messages.error(request, 'Unknown status.')
        return redirect('opsconsole:messages')
    old_status = msg.status
    msg.status = new_status
    msg.handled_by = request.user
    msg.save(update_fields=['status', 'handled_by'])
    AdminAuditLog.record(
        actor=request.user, action='contact_message_status', target=msg,
        detail={'from': old_status, 'to': new_status},
        ip_address=client_ip(request),
    )
    messages.success(request, f'Message marked {new_status}.')
    return redirect('opsconsole:messages')
