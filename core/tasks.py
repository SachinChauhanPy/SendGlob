"""
Production data purging.

Physically deletes soft-deleted records once they are older than
``SOFT_DELETE_PURGE_RETENTION_DAYS``. The purge is:

* dependency-aware - campaigns are purged before the templates/SMTP configs they
  reference, so SET_NULL/PROTECT relations never strand or block a delete;
* reference-safe - a template or SMTP config still referenced by ANY campaign
  (active or merely soft-deleted-but-not-yet-purged) is skipped, preserving
  historical accuracy;
* state-safe - campaigns that are queued/sending/scheduled are never purged,
  even if somehow soft-deleted, so in-flight work is never destroyed;
* audited - every run records a ``PurgeLog`` row.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, ProtectedError
from django.utils import timezone
from celery import shared_task

logger = logging.getLogger(__name__)


def _purge_by_retention(model, cutoff):
    """
    Hard-delete soft-deleted rows of ``model`` older than ``cutoff``. Use for
    file-bearing models that have no campaign reference to protect (their
    file-cleanup signals reclaim disk space on hard delete). Returns the count
    purged; skips and logs anything protected by a FK constraint.
    """
    purged = 0
    for obj in model.objects.filter(deleted_at__lt=cutoff):
        try:
            logger.info(f"[PURGE] Hard-deleting {model.__name__} ID={obj.pk}")
            obj.hard_delete()
            purged += 1
        except ProtectedError as e:
            logger.warning(f"[PURGE] {model.__name__} ID={obj.pk} protected, skipped: {e}")
    return purged


def _purge_unreferenced(model, cutoff):
    """
    Hard-delete soft-deleted rows of ``model`` older than ``cutoff`` that are no
    longer referenced by any campaign (counted via the reverse ``campaigns``
    accessor). Returns the count purged. Skips and logs anything still
    referenced or protected by a FK constraint.

    The reference count is annotated in a single query so eligibility is decided
    without a per-row ``exists()`` (the deletes themselves stay per-row to fire
    cascade and file-cleanup signals; see B-046).
    """
    purged = 0
    candidates = model.objects.filter(deleted_at__lt=cutoff).annotate(
        _campaign_count=Count('campaigns'),
    )
    for obj in candidates:
        if obj._campaign_count:
            logger.warning(
                f"[PURGE] Skipping {model.__name__} ID={obj.pk} - still referenced by a campaign."
            )
            continue
        try:
            logger.info(f"[PURGE] Hard-deleting {model.__name__} ID={obj.pk}")
            obj.hard_delete()
            purged += 1
        except ProtectedError as e:
            logger.warning(f"[PURGE] {model.__name__} ID={obj.pk} protected, skipped: {e}")
    return purged


@shared_task
def purge_soft_deleted_records():
    """
    Weekly task: physically remove soft-deleted records past the retention
    window, in FK-dependency order. Returns and logs a per-type summary, and
    writes a PurgeLog audit row.
    """
    from emails.models import EmailCampaign, EmailTemplate
    from documents.models import DocumentTemplate, Signature
    from accounts.models import SMTPConfig
    from core.models import PurgeLog

    retention_days = settings.SOFT_DELETE_PURGE_RETENTION_DAYS
    cutoff = timezone.now() - timedelta(days=retention_days)
    summary = {}

    # 1. Campaigns first. Cascades to Recipient/CampaignAttachment (and their
    #    file-cleanup signals). Any soft-deleted campaign is eligible EXCEPT the
    #    locked states (queued/sending/scheduled), so in-flight and scheduled work
    #    is never destroyed while abandoned drafts (and terminal campaigns) are
    #    reclaimed. LOCKED_STATUSES is the single source of truth for this.
    campaigns = 0
    eligible = EmailCampaign.objects.filter(deleted_at__lt=cutoff).exclude(
        status__in=EmailCampaign.LOCKED_STATUSES,
    )
    for campaign in eligible:
        try:
            logger.info(f"[PURGE] Hard-deleting campaign ID={campaign.pk} name='{campaign.name}'")
            campaign.hard_delete()
            campaigns += 1
        except ProtectedError as e:
            logger.warning(f"[PURGE] Campaign ID={campaign.pk} protected, skipped: {e}")
    summary['campaigns'] = campaigns

    # 2. Templates and SMTP configs, only once nothing references them.
    summary['email_templates'] = _purge_unreferenced(EmailTemplate, cutoff)
    summary['document_templates'] = _purge_unreferenced(DocumentTemplate, cutoff)
    summary['smtp_configs'] = _purge_unreferenced(SMTPConfig, cutoff)

    # 3. Signatures are referenced by name from CSV at generation time (no FK to
    #    protect), so they purge purely by retention. Their file-cleanup signals
    #    reclaim the original and processed images on hard delete.
    summary['signatures'] = _purge_by_retention(Signature, cutoff)

    PurgeLog.objects.create(
        retention_days=retention_days,
        campaigns_purged=summary['campaigns'],
        email_templates_purged=summary['email_templates'],
        document_templates_purged=summary['document_templates'],
        signatures_purged=summary['signatures'],
        smtp_configs_purged=summary['smtp_configs'],
        notes=f"cutoff={cutoff.isoformat()}",
    )

    logger.info(f"[PURGE] Completed. Summary: {summary}")
    return summary
