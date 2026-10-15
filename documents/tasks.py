from celery import shared_task
import logging

from django.core.files.base import ContentFile

from emails.models import EmailCampaign
from documents.models import DocumentTemplate, Signature
from documents.utils import save_document_for_recipient
from documents.signature_processing import (
    process_signature_bytes, SignatureProcessingError,
)

logger = logging.getLogger(__name__)


@shared_task
def process_signature_task(signature_id):
    """
    Run the background-removal pipeline for a ``Signature`` and store the clean
    transparent PNG. Updates ``processing_status``/``confidence_score`` and
    fails gracefully (status ``failed`` + message) rather than raising.
    """
    try:
        signature = Signature.objects.get(pk=signature_id)
    except Signature.DoesNotExist:
        logger.warning(f"Signature {signature_id} no longer exists; skipping processing.")
        return f"Skipped: signature {signature_id} not found."

    if signature.deleted_at is not None:
        logger.info(f"Signature {signature_id} soft-deleted; skipping processing.")
        return f"Skipped: signature {signature_id} soft-deleted."

    signature.processing_status = Signature.STATUS_PROCESSING
    signature.error_message = ""
    signature.save(update_fields=["processing_status", "error_message", "updated_at"])

    try:
        with signature.original_image.open("rb") as fh:
            raw = fh.read()
        png_bytes, confidence = process_signature_bytes(
            raw, signature.ink_color_mode, signature.ink_color,
        )
    except SignatureProcessingError as exc:
        signature.processing_status = Signature.STATUS_FAILED
        signature.error_message = str(exc)
        signature.save(update_fields=["processing_status", "error_message", "updated_at"])
        return f"Signature {signature_id} failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - unexpected; record and stop
        logger.error(f"Unexpected error processing signature {signature_id}: {exc}")
        signature.processing_status = Signature.STATUS_FAILED
        signature.error_message = "An unexpected error occurred while processing the image."
        signature.save(update_fields=["processing_status", "error_message", "updated_at"])
        return f"Signature {signature_id} errored: {exc}"

    signature.processed_image.save(
        f"signature_{signature.id}.png", ContentFile(png_bytes), save=False,
    )
    signature.confidence_score = confidence
    signature.processing_status = Signature.STATUS_READY
    signature.error_message = ""
    signature.save(update_fields=[
        "processed_image", "confidence_score", "processing_status",
        "error_message", "updated_at",
    ])
    return f"Signature {signature_id} ready (confidence {confidence:.2f})."


@shared_task
def generate_documents_task(campaign_id, document_template_id, auto_send):
    """
    Generate personalized documents for every (not-yet-sent) recipient of a
    campaign, then optionally queue the campaign for sending.

    Mirrors ``certificates.tasks.generate_certificates_task`` but renders the
    multi-field ``DocumentTemplate`` using the campaign's column mapping.
    """
    # Honour the global send kill switch: when outbound delivery is paused we do
    # not start generating documents either, so a paused system stays fully idle.
    from opsconsole import flags
    if flags.is_send_paused():
        logger.warning(
            f"Global send kill switch is on; deferring document generation for "
            f"campaign {campaign_id}."
        )
        return f"Deferred: kill switch on for campaign {campaign_id}."

    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
        if campaign.deleted_at is not None:
            logger.warning(f"Campaign {campaign_id} soft-deleted; aborting document generation.")
            return f"Aborted: Campaign {campaign_id} soft-deleted."
        template = DocumentTemplate.objects.get(pk=document_template_id)
    except Exception as e:
        logger.error(f"Error fetching campaign or document template: {e}")
        return f"Error: {e}"

    column_mapping = campaign.column_mapping or {}
    recipients = campaign.recipients.exclude(status='sent')
    generated = 0

    # B-054: template field configuration is fixed for the whole run, so fetch
    # it once instead of re-querying per recipient. The signature cache
    # likewise dedupes name -> Signature lookups across recipients.
    text_fields = list(template.text_fields.all().order_by('id'))
    signature_fields = list(template.signature_fields.all())
    signature_cache = {}

    # Credit metering: one credit per document produced (delivery is free). Check
    # affordability before each generation and debit only on success, so a failed
    # render never charges the user. Reuse the per-recipient failure reason so the
    # campaign surfaces "out of credits" instead of failing silently.
    from billing import credit_costs, wallet as credit_wallet
    out_of_credits = False

    for recipient in recipients:
        if not credit_wallet.can_afford(campaign.user, credit_costs.DOCUMENT_GENERATION):
            recipient.status = 'failed'
            recipient.error_message = 'Stopped: out of credits.'
            recipient.save()
            out_of_credits = True
            continue
        try:
            save_document_for_recipient(
                recipient, template, column_mapping, user=campaign.user,
                text_fields=text_fields, signature_fields=signature_fields,
                signature_cache=signature_cache,
            )
            try:
                credit_wallet.debit(
                    campaign.user, credit_costs.DOCUMENT_GENERATION,
                    transaction_type='debit_document', reference_id=str(campaign.id))
            except credit_wallet.InsufficientCreditsError:
                # Lost a race for the last credit; treat as out of credits.
                recipient.status = 'failed'
                recipient.error_message = 'Stopped: out of credits.'
                recipient.save()
                out_of_credits = True
                continue
            generated += 1
        except Exception as err:
            logger.error(f"Failed to generate document for {recipient.name}: {err}")
            recipient.status = 'failed'
            recipient.error_message = f"Document generation failed: {err}"
            recipient.save()

    campaign.total_emails = campaign.recipients.count()
    campaign.save()

    # If every recipient failed generation there is nothing to send. Mark the
    # campaign failed so it does not stay stuck in 'queued' indefinitely with
    # no email task ever dispatched.
    if generated == 0:
        campaign.status = 'failed'
        campaign.save(update_fields=['status'])
        logger.error(
            f"Document generation produced 0 files for campaign {campaign_id}; "
            f"marking campaign as failed."
        )
        return f"Document generation failed for all recipients in campaign {campaign_id}."

    if auto_send:
        campaign.status = 'queued'
        campaign.save(update_fields=['status'])
        from emails.tasks import send_campaign_emails_task
        send_campaign_emails_task.delay(campaign.id)
        return f"Generated {generated} documents and queued campaign {campaign_id}."

    return f"Generated {generated} documents for campaign {campaign_id}."
