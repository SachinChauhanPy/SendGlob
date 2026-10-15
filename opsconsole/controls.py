"""Audited operator control actions.

Every function here performs one privileged action and writes exactly one
``AdminAuditLog`` row on success. Actions are intentionally small and return a
``(ok: bool, message: str)`` tuple so views stay thin and consistent. Campaign
actions are routed through the same validated transitions the user-facing app
uses (no raw field edits that could bypass lifecycle invariants), and never
operate on soft-deleted campaigns.

Authorization is enforced at the view layer (``operator_required``); these
functions assume the caller is already an authorized operator and focus on
correctness, validation, and auditing.
"""
import logging

from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from .flags import THROTTLE, invalidate
from .models import AdminAuditLog, OperationalFlag

logger = logging.getLogger(__name__)


# --- Operational flags --------------------------------------------------------
def set_flag(*, actor, key, enabled, value='', reason='', ip_address=None):
    """Upsert an OperationalFlag, invalidate its cache, and audit the change."""
    flag, _ = OperationalFlag.objects.get_or_create(key=key)
    before = {'enabled': flag.enabled, 'value': flag.value}
    flag.enabled = bool(enabled)
    flag.value = value or ''
    flag.updated_by = actor
    flag.save()
    invalidate(key)
    AdminAuditLog.record(
        actor=actor, action=f'flag_{key}', target=flag, reason=reason,
        detail={'before': before, 'after': {'enabled': flag.enabled, 'value': flag.value}},
        ip_address=ip_address,
    )
    return True, f"Flag '{key}' is now {'on' if flag.enabled else 'off'}."


def set_throttle(*, actor, seconds, reason='', ip_address=None):
    """Set or clear the global send-throttle override (seconds, empty to clear)."""
    enabled = seconds not in (None, '')
    return set_flag(actor=actor, key=THROTTLE, enabled=enabled,
                    value=str(seconds) if enabled else '', reason=reason,
                    ip_address=ip_address)


# --- Credits ------------------------------------------------------------------
def grant_credits(*, actor, user, amount, reason='', ip_address=None):
    """Grant (or adjust, if negative) a user's credits via the billing ledger."""
    from billing.services import grant_credits as _grant
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False, 'Amount must be a whole number.'
    if amount == 0:
        return False, 'Amount must be non-zero.'
    from billing.wallet import InsufficientCreditsError
    try:
        txn = _grant(user, amount, actor=actor, reason=reason)
    except (ValueError, InsufficientCreditsError) as exc:
        return False, str(exc)
    AdminAuditLog.record(
        actor=actor, action='grant_credits', target=user, reason=reason,
        detail={'amount': amount, 'balance_after': txn.balance_after},
        ip_address=ip_address,
    )
    return True, f"Granted {amount:+d} credits to {user.email} (balance {txn.balance_after})."


# --- User management ----------------------------------------------------------
def set_user_active(*, actor, user, active, reason='', ip_address=None):
    """Suspend (active=False) or reactivate (active=True) a user. Suspending also
    force-logs-out their active sessions so the change takes effect immediately."""
    if actor.pk == user.pk:
        return False, 'You cannot change your own active status here.'
    user.is_active = bool(active)
    user.save(update_fields=['is_active'])
    action = 'reactivate_user' if active else 'suspend_user'
    AdminAuditLog.record(actor=actor, action=action, target=user, reason=reason,
                         ip_address=ip_address)
    if not active:
        _drop_sessions(user)
    verb = 'reactivated' if active else 'suspended'
    return True, f"User {user.email} {verb}."


def force_logout(*, actor, user, reason='', ip_address=None):
    """Invalidate all of a user's active sessions."""
    count = _drop_sessions(user)
    AdminAuditLog.record(actor=actor, action='force_logout', target=user, reason=reason,
                         detail={'sessions_cleared': count}, ip_address=ip_address)
    return True, f"Cleared {count} active session(s) for {user.email}."


def _drop_sessions(user):
    """Delete the user's unexpired sessions (DB session backend)."""
    cleared = 0
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now):
        if str(session.get_decoded().get('_auth_user_id')) == str(user.pk):
            session.delete()
            cleared += 1
    return cleared


# --- Campaign control ---------------------------------------------------------
# Lifecycle (draft, scheduled, queued, sending, completed, failed, cancelled) has
# no 'paused' state and we do not introduce one (CLAUDE.md). Operator "pause" maps
# to cancel; "resume/restart" maps to requeue.
def cancel_campaign(*, actor, campaign, reason='', ip_address=None):
    """Stop an in-flight or scheduled campaign. Valid from queued/sending/scheduled."""
    if campaign.deleted_at is not None:
        return False, 'Campaign is deleted; restore it before taking actions.'
    if campaign.status not in ('queued', 'sending', 'scheduled'):
        return False, f"Cannot cancel a campaign in '{campaign.status}' status."
    campaign.status = 'cancelled'
    campaign.save(update_fields=['status'])
    AdminAuditLog.record(actor=actor, action='cancel_campaign', target=campaign,
                         reason=reason, ip_address=ip_address)
    return True, f"Campaign '{campaign.name}' cancelled."


def requeue_campaign(*, actor, campaign, reason='', ip_address=None):
    """Requeue a stuck/failed/cancelled campaign for sending, resetting failed and
    abandoned recipients to pending. Routes through the same task the app uses."""
    if campaign.deleted_at is not None:
        return False, 'Campaign is deleted; restore it before taking actions.'
    if campaign.status not in ('failed', 'cancelled'):
        return False, f"Requeue is only valid from failed/cancelled (got '{campaign.status}')."
    total = campaign.recipients.count()
    if total == 0:
        return False, 'Campaign has no recipients to send.'
    if campaign.recipients.filter(status='sent').count() == total:
        return False, 'All recipients already sent; nothing to requeue.'

    with transaction.atomic():
        campaign.recipients.filter(status__in=['failed', 'sending']).update(status='pending')
        campaign.status = 'queued'
        campaign.next_run_at = None
        campaign.reminder_sent_at = None
        campaign.sent_emails = campaign.recipients.filter(status='sent').count()
        campaign.failed_emails = 0
        campaign.total_emails = total
        campaign.save()

    if campaign.is_document_campaign and campaign.document_template:
        from documents.tasks import generate_documents_task
        generate_documents_task.delay(campaign.id, campaign.document_template.id, True)
    else:
        from emails.tasks import send_campaign_emails_task
        send_campaign_emails_task.delay(campaign.id)

    AdminAuditLog.record(actor=actor, action='requeue_campaign', target=campaign,
                         reason=reason, ip_address=ip_address)
    return True, f"Campaign '{campaign.name}' requeued for sending."
