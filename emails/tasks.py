import os
import time
import logging
import mimetypes
from datetime import timedelta
from smtplib import SMTPException, SMTPAuthenticationError

from django.utils import timezone
from django.urls import reverse
from django.db import connection as db_connection, transaction
from celery import shared_task
from django.core.mail import get_connection, EmailMultiAlternatives
from django.core.files.base import ContentFile
from django.template import Context, Template
from django.contrib.sites.models import Site
from django.conf import settings

from emails.models import EmailCampaign, CampaignAttachment, Recipient
from emails.scheduling import compute_next_run, describe_schedule

logger = logging.getLogger(__name__)

# A recipient left in 'sending' for longer than this is treated as abandoned
# (e.g. the worker died mid-batch) and is reset so it can be re-attempted.
STALE_SENDING_THRESHOLD = timedelta(minutes=10)

# Bounded retry budget for transient SMTP connection failures.
SMTP_CONNECT_MAX_ATTEMPTS = 4
SMTP_CONNECT_BACKOFF_BASE = 1  # seconds; doubled each attempt


def render_template_string(template_str, context_dict):
    """Render variables inside a template string using Django's template engine."""
    try:
        t = Template(template_str)
        c = Context(context_dict)
        return t.render(c)
    except Exception as e:
        logger.error(f"Error rendering template string: {e}")
        return template_str


def get_site_url():
    """Retrieve the base URL of the site, checking settings, env, or database."""
    # 1. Check settings or environment variable
    site_url = getattr(settings, 'SITE_URL', os.getenv('SITE_URL', None))
    if site_url:
        return site_url.rstrip('/')

    # 2. Fall back to Django Sites framework
    try:
        domain = Site.objects.get_current().domain
        if not domain.startswith(('http://', 'https://')):
            return f"http://{domain}"
        return domain
    except Exception:
        # 3. Fallback to standard localhost
        return "http://127.0.0.1:8000"


def build_tracking_pixel(site_url, tracking_uuid):
    """
    Build the open-tracking pixel <img> tag.

    The pixel path is resolved with reverse() so it always matches the live URL
    configuration (the route lives under /dashboard/ since emails.urls was
    remounted there); a hardcoded path would silently 404 after such moves.
    """
    path = reverse('emails:email_open_track', kwargs={'tracking_uuid': tracking_uuid})
    url = f"{site_url}{path}"
    return (
        f'<img src="{url}" width="1" height="1" alt="" '
        f'style="display:none; visibility:hidden;">'
    )


def _sync_campaign_stats(campaign):
    """Recompute and persist the campaign's aggregate delivery counters."""
    campaign.sent_emails = campaign.recipients.filter(status='sent').count()
    campaign.failed_emails = campaign.recipients.filter(status='failed').count()
    campaign.total_emails = campaign.recipients.count()
    campaign.save(update_fields=['sent_emails', 'failed_emails', 'total_emails'])


def reset_stale_sending(campaign):
    """
    Reset recipients abandoned in 'sending' back to 'retrying' so they are
    re-attempted instead of being silently lost when a worker dies mid-batch.

    Returns the number of rows recovered.
    """
    cutoff = timezone.now() - STALE_SENDING_THRESHOLD
    return Recipient.objects.filter(
        campaign=campaign,
        status='sending',
        updated_at__lt=cutoff,
    ).update(status='retrying')


def _begin_campaign_send(campaign_id):
    """
    Lock the campaign row, validate its state, and flip 'queued' -> 'sending'.

    The row-level lock prevents two workers from both passing the status check
    and double-sending the same campaign. select_for_update is a no-op on
    SQLite (dev) and only enforced on backends that support it (PostgreSQL).
    Returns the campaign, or None if it is not in a sendable state.
    """
    with transaction.atomic():
        qs = EmailCampaign.objects.all()
        if db_connection.features.has_select_for_update:
            qs = qs.select_for_update()
        try:
            campaign = qs.get(pk=campaign_id)
        except EmailCampaign.DoesNotExist:
            logger.error(f"Campaign with ID {campaign_id} does not exist.")
            return None

        if campaign.deleted_at is not None:
            logger.warning(
                f"Campaign with ID {campaign_id} has been soft-deleted. Aborting send."
            )
            return None

        if campaign.status not in ['queued', 'sending']:
            logger.info(
                f"Campaign '{campaign.name}' is in status '{campaign.status}'. Skipping."
            )
            return None

        if campaign.status == 'queued':
            campaign.status = 'sending'
            campaign.started_at = timezone.now()
            campaign.save(update_fields=['status', 'started_at'])

        return campaign


def _claim_recipient_batch(campaign, batch_size):
    """
    Atomically claim up to ``batch_size`` pending/retrying recipients, flipping
    them to 'sending' so a concurrent worker cannot also pick them up.

    skip_locked lets a second worker get a disjoint batch instead of blocking;
    both locking and skip_locked are only honoured on capable backends
    (PostgreSQL) and degrade to a plain query on SQLite. Ordering by id keeps
    send order deterministic across backends.
    """
    with transaction.atomic():
        qs = Recipient.objects.filter(
            campaign=campaign,
            status__in=['pending', 'retrying'],
        ).order_by('id')
        if db_connection.features.has_select_for_update:
            if db_connection.features.has_select_for_update_skip_locked:
                qs = qs.select_for_update(skip_locked=True)
            else:
                qs = qs.select_for_update()

        claimed = list(qs[:batch_size])
        if claimed:
            claimed_ids = [r.id for r in claimed]
            # bulk update() bypasses auto_now, so set updated_at explicitly to
            # mark the claim time and keep the stale-recipient reaper from
            # reclaiming rows another worker just took.
            now = timezone.now()
            Recipient.objects.filter(id__in=claimed_ids).update(status='sending', updated_at=now)
            for r in claimed:
                r.status = 'sending'
                r.updated_at = now
    return claimed


def _release_recipients(recipient_ids, status='retrying', error_message=None):
    """Return claimed recipients to a re-attemptable state (or mark them failed)."""
    if not recipient_ids:
        return
    fields = {'status': status}
    if error_message is not None:
        fields['error_message'] = error_message
    Recipient.objects.filter(id__in=recipient_ids).update(**fields)


def _open_smtp_connection(smtp_config, password):
    """
    Open an SMTP connection, retrying transient failures with exponential
    backoff. Authentication errors are terminal and raised immediately; only
    network/handshake errors are retried. Raises the last error if the retry
    budget is exhausted.
    """
    delay = SMTP_CONNECT_BACKOFF_BASE
    last_error = None
    for attempt in range(1, SMTP_CONNECT_MAX_ATTEMPTS + 1):
        connection = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=smtp_config.smtp_host,
            port=smtp_config.smtp_port,
            username=smtp_config.smtp_username,
            password=password,
            use_tls=smtp_config.use_tls,
            timeout=30,
        )
        try:
            connection.open()
            return connection
        except SMTPAuthenticationError:
            # Invalid app password / revoked credentials - retrying cannot help.
            raise
        except (SMTPException, OSError) as e:
            last_error = e
            logger.warning(
                f"SMTP connect attempt {attempt}/{SMTP_CONNECT_MAX_ATTEMPTS} "
                f"for {smtp_config.smtp_username} failed: {e}"
            )
            if attempt < SMTP_CONNECT_MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
    raise last_error


def _copy_attachments(source, clone):
    """Duplicate ``source``'s attachments onto ``clone`` with independent file
    copies so each campaign owns its own storage. A read failure on one
    attachment is logged and skipped rather than aborting the recurrence."""
    for attachment in source.attachments.all():
        try:
            attachment.file.open('rb')
            try:
                content = attachment.file.read()
            finally:
                attachment.file.close()
        except Exception as exc:
            logger.error(
                "Recurrence: failed to copy attachment %s for campaign %s: %s",
                attachment.pk, source.pk, exc,
            )
            continue

        new_attachment = CampaignAttachment(campaign=clone, filename=attachment.filename)
        # save=True writes a fresh file; storage de-duplicates the name so the
        # clone never shares a path with the source.
        new_attachment.file.save(
            os.path.basename(attachment.file.name), ContentFile(content), save=True,
        )


def arm_next_cycle(campaign):
    """
    When a recurring campaign completes, create the next 'scheduled' instance,
    carrying its recipients, template, content, document config and attachments
    forward as a fresh pending batch. Idempotent: guarded by the
    recurrence_children reverse relation so a re-run/restart cannot fork the chain.

    Attachment files are copied to new storage paths per cycle (a true copy, not a
    shared reference) so each campaign owns its own files. This keeps the
    post_delete file-cleanup signal safe: deleting one cycle never removes a file
    another cycle still references (B-039).
    """
    if not campaign.is_recurring or campaign.schedule_type == EmailCampaign.SCHEDULE_NONE:
        return None
    if campaign.recurrence_children.exists():
        return None

    now = timezone.now()
    base = campaign.completed_at or now
    next_at = compute_next_run(campaign, base=base, now=now)
    if next_at is None:
        logger.warning(
            f"Recurrence: campaign {campaign.pk} has no computable next run; chain stops."
        )
        return None

    clone = EmailCampaign.objects.create(
        user=campaign.user,
        name=campaign.name,
        template=campaign.template,
        smtp_config=campaign.smtp_config,
        status='scheduled',
        is_document_campaign=campaign.is_document_campaign,
        document_template=campaign.document_template,
        column_mapping=campaign.column_mapping,
        schedule_type=campaign.schedule_type,
        schedule_time=campaign.schedule_time,
        is_recurring=True,
        interval_days=campaign.interval_days,
        day_of_month=campaign.day_of_month,
        day_of_week=campaign.day_of_week,
        next_run_at=next_at,
        parent_campaign=campaign.parent_campaign or campaign,
    )

    carried = [
        Recipient(
            campaign=clone,
            email=r.email,
            name=r.name,
            context_data=r.context_data,
            status='pending',
        )
        for r in campaign.recipients.all()
    ]
    if carried:
        Recipient.objects.bulk_create(carried)
        clone.total_emails = len(carried)
        clone.save(update_fields=['total_emails'])

    _copy_attachments(campaign, clone)

    campaign.next_run_at = next_at
    campaign.save(update_fields=['next_run_at'])
    logger.info(
        f"Recurrence: campaign {campaign.pk} armed next instance {clone.pk} "
        f"({len(carried)} recipients) for {next_at.isoformat()}."
    )
    return clone


def _finalize_if_complete(campaign):
    """
    Mark the campaign 'completed' only when no recipient remains in a
    non-terminal state ('pending', 'retrying', or 'sending'). Counting
    'sending' prevents a campaign from being flagged complete while recipients
    are still in flight or were abandoned by a dead worker.

    Returns True if there is claimable work right now (so the caller should
    keep looping), False otherwise.
    """
    remaining = Recipient.objects.filter(
        campaign=campaign,
        status__in=['pending', 'retrying', 'sending'],
    ).count()

    if remaining == 0:
        campaign.status = 'completed'
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=['status', 'completed_at'])
        logger.info(f"Campaign '{campaign.name}' completed sending.")
        arm_next_cycle(campaign)
        return False

    # Work that can be picked up immediately. Stale 'sending' rows that are not
    # yet past the recovery threshold leave the campaign in 'sending' (not
    # 'completed') and are recovered on the next send invocation / reaper run.
    return Recipient.objects.filter(
        campaign=campaign,
        status__in=['pending', 'retrying'],
    ).exists()


def send_campaign_emails(campaign_id, batch_size=10, max_retries=3, throttle_delay=1.5):
    """
    Process a single batch of recipients for a campaign: claim the batch,
    establish one SMTP session, render and send each email (with open-tracking
    pixel and attachments), and update per-recipient and campaign state.

    Returns True if more claimable work remains, False otherwise.
    """
    campaign = _begin_campaign_send(campaign_id)
    if campaign is None:
        return False

    # Recover recipients abandoned mid-send by a previously dead worker.
    reset_stale_sending(campaign)
    _sync_campaign_stats(campaign)

    claimed = _claim_recipient_batch(campaign, batch_size)
    if not claimed:
        return _finalize_if_complete(campaign)

    claimed_ids = [r.id for r in claimed]

    # Retrieve SMTP password
    smtp_config = campaign.smtp_config
    password = smtp_config.get_app_password()
    if not password:
        campaign.status = 'failed'
        campaign.save(update_fields=['status'])
        _release_recipients(
            claimed_ids,
            status='failed',
            error_message="Decryption of SMTP credentials failed.",
        )
        logger.error(
            f"Failed to decrypt SMTP credentials for campaign '{campaign.name}'. Aborting."
        )
        return False

    # Establish SMTP connection with bounded retry/backoff for transient errors.
    try:
        connection = _open_smtp_connection(smtp_config, password)
    except SMTPAuthenticationError as e:
        campaign.status = 'failed'
        campaign.save(update_fields=['status'])
        # Release the claim so a future restart re-sends these recipients.
        _release_recipients(claimed_ids, status='retrying')
        logger.error(
            f"SMTP authentication failed for campaign '{campaign.name}': {e}"
        )
        return False
    except Exception as e:
        campaign.status = 'failed'
        campaign.save(update_fields=['status'])
        _release_recipients(claimed_ids, status='retrying')
        logger.error(
            f"Failed to open SMTP connection for campaign '{campaign.name}' "
            f"after {SMTP_CONNECT_MAX_ATTEMPTS} attempts: {e}"
        )
        return False

    site_url = get_site_url()

    for recipient in claimed:
        # Context for template rendering
        context = {
            'name': recipient.name,
            'email': recipient.email,
        }
        # Merge JSON context fields
        if isinstance(recipient.context_data, dict):
            context.update(recipient.context_data)

        # Render Subject & Body
        subject = render_template_string(campaign.template.subject, context) if campaign.template else campaign.name
        html_body = render_template_string(campaign.template.body_html, context) if campaign.template else ""
        text_body = render_template_string(campaign.template.body_text, context) if campaign.template else ""

        # Inject open-tracking pixel in HTML
        tracking_pixel = build_tracking_pixel(site_url, recipient.tracking_uuid)
        if html_body:
            # Inject before closing body tag if exists, otherwise append
            if "</body>" in html_body:
                html_body = html_body.replace("</body>", f"{tracking_pixel}</body>")
            else:
                html_body += tracking_pixel
        else:
            # Fallback if text-only, we can append tracking pixel (though mostly works in HTML viewers)
            html_body = f"<html><body>{text_body.replace(chr(10), '<br>')}{tracking_pixel}</body></html>"

        try:
            # Prepare email message
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body or "HTML version only.",
                from_email=smtp_config.smtp_username,
                to=[recipient.email],
                connection=connection
            )
            msg.attach_alternative(html_body, "text/html")

            # Attach campaign attachments
            for attachment in campaign.attachments.all():
                try:
                    attachment.file.seek(0)
                    content = attachment.file.read()
                    mime_type, _ = mimetypes.guess_type(attachment.filename)
                    msg.attach(attachment.filename, content, mime_type or 'application/octet-stream')
                except Exception as attach_err:
                    logger.error(f"Error attaching campaign file {attachment.filename}: {attach_err}")

            # Attach generated document if applicable
            if campaign.is_document_campaign and recipient.document_file:
                try:
                    recipient.document_file.seek(0)
                    doc_content = recipient.document_file.read()
                    output_format = (
                        campaign.document_template.output_format
                        if campaign.document_template
                        else 'pdf'
                    )
                    ext = 'pdf' if output_format == 'pdf' else 'png'
                    mime_type = 'application/pdf' if ext == 'pdf' else 'image/png'
                    safe_name = (recipient.name or 'document').replace(' ', '_')
                    msg.attach(f"{safe_name}.{ext}", doc_content, mime_type)
                except Exception as doc_attach_err:
                    logger.error(f"Error attaching document for {recipient.name}: {doc_attach_err}")

            # Send email
            msg.send()

            # Record success
            recipient.status = 'sent'
            recipient.sent_at = timezone.now()
            recipient.error_message = None
            recipient.save()

        except Exception as send_err:
            logger.warning(f"Error sending email to {recipient.email}: {send_err}")
            recipient.retry_count += 1
            if recipient.retry_count < max_retries:
                recipient.status = 'retrying'
            else:
                recipient.status = 'failed'
            recipient.error_message = str(send_err)
            recipient.save()

        # Update stats on campaign dynamically
        campaign.sent_emails = campaign.recipients.filter(status='sent').count()
        campaign.failed_emails = campaign.recipients.filter(status='failed').count()
        campaign.save(update_fields=['sent_emails', 'failed_emails'])

        # Throttling to respect Gmail rate limits
        if throttle_delay > 0:
            time.sleep(throttle_delay)

    # Close SMTP session
    try:
        connection.close()
    except Exception:
        pass

    return _finalize_if_complete(campaign)


def send_entire_campaign(campaign_id, batch_size=15, throttle_delay=1.5):
    """Keep sending batches in a loop until the campaign has no more pending recipients."""
    from opsconsole import flags

    while True:
        # Global send kill switch: stop dispatching immediately, leaving pending
        # recipients untouched so the campaign can be requeued once the switch is
        # cleared. Checked every batch so a toggle takes effect within seconds.
        if flags.is_send_paused():
            logger.warning(
                f"Global send kill switch is on; pausing campaign {campaign_id}."
            )
            break

        try:
            campaign = EmailCampaign.objects.get(pk=campaign_id)
            if campaign.status not in ['queued', 'sending']:
                break
        except EmailCampaign.DoesNotExist:
            break

        # Operators can override the inter-send delay during an incident without a
        # redeploy; falls back to the caller's default when unset.
        effective_throttle = flags.send_throttle_override(default=throttle_delay)
        has_more = send_campaign_emails(campaign_id, batch_size=batch_size, throttle_delay=effective_throttle)
        if not has_more:
            break
        time.sleep(2)


@shared_task
def send_campaign_emails_task(campaign_id):
    """
    Celery task to send all pending campaign emails.
    """
    send_entire_campaign(campaign_id)
    return f"Campaign {campaign_id} sending finished."


@shared_task
def reap_stale_sending_recipients_task():
    """
    Periodic safety net: reset recipients abandoned in 'sending' (worker died
    mid-batch) across all active campaigns so they are re-attempted. Intended
    to be scheduled via Celery beat; also runs opportunistically at the start
    of every send batch via reset_stale_sending().
    """
    cutoff = timezone.now() - STALE_SENDING_THRESHOLD
    recovered = Recipient.objects.filter(
        status='sending',
        updated_at__lt=cutoff,
        campaign__status__in=['queued', 'sending'],
    ).update(status='retrying')
    if recovered:
        logger.info(f"Reaper reset {recovered} stale 'sending' recipient(s) to 'retrying'.")
    return recovered


@shared_task
def trigger_scheduled_campaigns():
    """
    Celery Beat task (runs every minute): promote 'scheduled' campaigns whose
    next_run_at has passed to 'queued' and dispatch the appropriate worker.

    Uses a compare-and-set update (filter on status='scheduled' before flipping)
    so two overlapping beat ticks cannot both promote - and therefore cannot
    double-dispatch - the same campaign.
    """
    now = timezone.now()
    due = EmailCampaign.objects.filter(
        status='scheduled',
        next_run_at__isnull=False,
        next_run_at__lte=now,
        deleted_at__isnull=True,
    )

    promoted = 0
    for campaign in due:
        # B-051 defense in depth: never promote a campaign with no recipients.
        # Demote it to draft and disarm the schedule so Beat stops retrying it.
        if not campaign.recipients.exists():
            EmailCampaign.objects.filter(pk=campaign.pk, status='scheduled').update(
                status='draft', next_run_at=None
            )
            logger.warning(
                f"trigger_scheduled_campaigns: campaign {campaign.pk} was scheduled "
                "with zero recipients; demoted to draft."
            )
            continue

        flipped = EmailCampaign.objects.filter(
            pk=campaign.pk, status='scheduled'
        ).update(status='queued', started_at=now)
        if not flipped:
            continue  # another worker already promoted it

        if campaign.is_document_campaign and campaign.document_template_id:
            from documents.tasks import generate_documents_task
            generate_documents_task.delay(campaign.id, campaign.document_template_id, True)
        else:
            send_campaign_emails_task.delay(campaign.id)
        promoted += 1

    if promoted:
        logger.info(f"trigger_scheduled_campaigns: promoted {promoted} scheduled campaign(s) to queued.")
    return promoted


def _send_schedule_reminder(campaign):
    """Email the campaign owner that a scheduled run is approaching. Uses the
    project email backend (not the user's sending SMTP), since this is a system
    notification about their own campaign."""
    from django.core.mail import send_mail

    run_local = timezone.localtime(campaign.next_run_at)
    subject = f"Your campaign '{campaign.name}' runs soon"
    body = (
        f"Your campaign '{campaign.name}' is scheduled to run at "
        f"{run_local.strftime('%b %d, %Y %H:%M %Z')}.\n"
        f"Schedule: {describe_schedule(campaign)}\n\n"
        f"You can edit recipients, the email template or content until "
        f"{settings.SCHEDULE_EDIT_FREEZE_HOURS} hours before the run. After that "
        f"the campaign locks and runs as configured.\n\n"
        f"Campaign ID: {campaign.pk}\n"
    )
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [campaign.user.email],
        fail_silently=False,
    )


@shared_task
def notify_upcoming_scheduled_campaigns():
    """
    Celery Beat task (hourly): email the owner once when a scheduled campaign
    enters the pre-run edit-freeze window, then mark reminder_sent_at so the
    reminder is never re-sent. After this point the campaign is locked for
    editing (see EmailCampaign.is_locked).
    """
    now = timezone.now()
    lead = timedelta(hours=settings.SCHEDULE_REMINDER_LEAD_HOURS)
    due = EmailCampaign.objects.filter(
        status='scheduled',
        next_run_at__isnull=False,
        next_run_at__gt=now,
        next_run_at__lte=now + lead,
        reminder_sent_at__isnull=True,
        deleted_at__isnull=True,
    )

    sent = 0
    for campaign in due:
        # Compare-and-set so overlapping ticks send at most one reminder.
        claimed = EmailCampaign.objects.filter(
            pk=campaign.pk, reminder_sent_at__isnull=True
        ).update(reminder_sent_at=now)
        if not claimed:
            continue
        try:
            _send_schedule_reminder(campaign)
            sent += 1
        except Exception as e:
            logger.error(
                f"Failed to send schedule reminder for campaign {campaign.pk}: {e}"
            )

    if sent:
        logger.info(f"notify_upcoming_scheduled_campaigns: sent {sent} reminder(s).")
    return sent
