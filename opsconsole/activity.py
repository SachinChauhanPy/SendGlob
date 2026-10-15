"""Unified activity feed for the console.

Aggregates already-stored records (campaigns, sent recipients, signups, audit
rows) into a single time-ordered stream. Phase A is read-only; the credit ledger
and payment events join the same feed in later phases.

Each item is a plain dict with a common shape so the template renders one list:
``{'when', 'kind', 'icon', 'summary', 'actor', 'url'}``.
"""
from itertools import chain
from operator import itemgetter

from django.urls import reverse


def _campaign_events(limit):
    from emails.models import EmailCampaign

    # ``objects`` is intentional: the operator feed must include soft-deleted
    # campaigns too (sanctioned use of the unfiltered manager). The model has no
    # single "last changed" column, so order by created_at at the DB level and
    # derive the most recent meaningful timestamp per row for the merged sort.
    rows = (
        EmailCampaign.objects.select_related('user')
        .order_by('-created_at')[:limit]
    )
    items = []
    for c in rows:
        when = c.completed_at or c.started_at or c.created_at
        items.append({
            'when': when,
            'kind': 'campaign',
            'icon': 'mails',
            'summary': f'Campaign "{c.name}" is {c.get_status_display()}',
            'actor': getattr(c.user, 'email', ''),
            'url': '',
        })
    return items


def _signup_events(limit):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    rows = User.objects.order_by('-date_joined')[:limit]
    items = []
    for u in rows:
        items.append({
            'when': u.date_joined,
            'kind': 'signup',
            'icon': 'user-plus',
            'summary': f'New user {u.email}',
            'actor': u.email,
            'url': '',
        })
    return items


def _audit_events(limit):
    from .models import AdminAuditLog

    rows = AdminAuditLog.objects.select_related('actor')[:limit]
    items = []
    for a in rows:
        target = f' on {a.target_type}#{a.target_id}' if a.target_type else ''
        items.append({
            'when': a.created_at,
            'kind': 'audit',
            'icon': 'shield',
            'summary': f'Operator action: {a.action}{target}',
            'actor': getattr(a.actor, 'email', ''),
            'url': reverse('opsconsole:audit'),
        })
    return items


def recent_activity(limit=40):
    """Merge the per-source streams into one time-descending feed."""
    per_source = max(limit, 20)
    merged = chain(
        _campaign_events(per_source),
        _signup_events(per_source),
        _audit_events(per_source),
    )
    ordered = sorted(merged, key=itemgetter('when'), reverse=True)
    return ordered[:limit]
