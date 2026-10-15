import io
import base64
import logging
import mimetypes

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile
from django.http import HttpResponse, Http404, JsonResponse, FileResponse

from documents.models import DocumentTemplate, Signature
from documents.forms import (
    DocumentTemplateForm, DocumentTextFieldFormSet, DocumentCampaignForm,
    DocumentSignatureFieldFormSet, SignatureForm,
)
from documents.utils import generate_document_image
from documents.tasks import generate_documents_task, process_signature_task
from emails.models import EmailCampaign, Recipient
from emails.scheduling import describe_schedule
from emails.views import parse_csv_recipients
from accounts.models import SMTPConfig

logger = logging.getLogger(__name__)


@login_required
def document_list(value_request):
    templates = DocumentTemplate.active.filter(user=value_request.user).order_by('-created_at')
    return render(value_request, 'documents/document_list.html', {'templates': templates})


@login_required
def document_create(value_request):
    if value_request.method == 'POST':
        form = DocumentTemplateForm(value_request.POST, value_request.FILES)
        formset = DocumentTextFieldFormSet(value_request.POST, value_request.FILES)
        sig_formset = DocumentSignatureFieldFormSet(value_request.POST, value_request.FILES)
        # Signature fields are optional; only validate them when the editor
        # actually submitted that formset (keeps text-only clients working).
        sig_present = f'{sig_formset.prefix}-TOTAL_FORMS' in value_request.POST
        if (form.is_valid() and formset.is_valid()
                and (not sig_present or sig_formset.is_valid())):
            with transaction.atomic():
                template = form.save(commit=False)
                template.user = value_request.user
                template.save()
                formset.instance = template
                formset.save()
                if sig_present:
                    sig_formset.instance = template
                    sig_formset.save()
            messages.success(value_request, "Document template created successfully.")
            return redirect('documents:list')
    else:
        form = DocumentTemplateForm()
        formset = DocumentTextFieldFormSet()
        sig_formset = DocumentSignatureFieldFormSet()
    return render(value_request, 'documents/document_form.html', {
        'form': form, 'formset': formset, 'sig_formset': sig_formset,
        'title': 'Create Document Template',
    })


@login_required
def document_edit(value_request, pk):
    template = get_object_or_404(DocumentTemplate.active, pk=pk, user=value_request.user)

    # Block editing while an active campaign depends on this template.
    if template.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot edit this template because it is in use by a scheduled or active campaign.")
        return redirect('documents:list')

    if value_request.method == 'POST':
        form = DocumentTemplateForm(value_request.POST, value_request.FILES, instance=template)
        formset = DocumentTextFieldFormSet(value_request.POST, value_request.FILES, instance=template)
        sig_formset = DocumentSignatureFieldFormSet(value_request.POST, value_request.FILES, instance=template)
        sig_present = f'{sig_formset.prefix}-TOTAL_FORMS' in value_request.POST
        if (form.is_valid() and formset.is_valid()
                and (not sig_present or sig_formset.is_valid())):
            with transaction.atomic():
                template = form.save()
                formset.save()
                if sig_present:
                    sig_formset.save()

            # Regenerate documents for linked, not-yet-sent campaigns that are still editable.
            campaigns = template.campaigns.filter(
                deleted_at__isnull=True, status__in=['draft', 'failed', 'cancelled'],
            )
            for campaign in campaigns:
                # B-054: reset stale documents with one bulk UPDATE instead of a
                # save() per recipient. Bulk update bypasses the pre_save file
                # cleanup signal, so the physical files are deleted explicitly
                # first (same pattern as campaign_restart, see B-043).
                stale = campaign.recipients.exclude(status='sent').exclude(document_file='')
                for r in stale:
                    try:
                        r.document_file.delete(save=False)
                    except Exception as e:
                        logger.error(f"Error deleting stale document file during template edit: {e}")
                stale.update(document_file=None, status='pending')
                generate_documents_task.delay(campaign.id, template.id, False)

            messages.success(value_request, "Document template updated. Regeneration started for linked draft campaigns.")
            return redirect('documents:list')
    else:
        form = DocumentTemplateForm(instance=template)
        formset = DocumentTextFieldFormSet(instance=template)
        sig_formset = DocumentSignatureFieldFormSet(instance=template)
    return render(value_request, 'documents/document_form.html', {
        'form': form, 'formset': formset, 'sig_formset': sig_formset,
        'title': 'Edit Document Template',
    })


@login_required
def document_delete(value_request, pk):
    template = get_object_or_404(DocumentTemplate.active, pk=pk, user=value_request.user)
    if template.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot delete this template because it is in use by a scheduled or active campaign.")
        return redirect('documents:list')
    if value_request.method == 'POST':
        template.delete()
        messages.success(value_request, "Document template deleted successfully.")
    return redirect('documents:list')


@login_required
def document_preview(value_request, pk):
    """Render a live PNG preview with sample text in each field so users can verify placement."""
    template = get_object_or_404(DocumentTemplate.active, pk=pk, user=value_request.user)
    values = {}
    for field in template.text_fields.all():
        if field.field_name == 'name':
            values[field.field_name] = 'John Doe'
        else:
            values[field.field_name] = field.label or field.field_name
    try:
        img = generate_document_image(template, values, placeholder_signatures=True)
        out = io.BytesIO()
        img.save(out, format='PNG')
        out.seek(0)
        return HttpResponse(out.getvalue(), content_type='image/png')
    except Exception as e:
        logger.error(f"Error generating preview for document template {template.id}: {e}")
        raise Http404("Failed to generate preview image.")


@login_required
def document_template_fields(value_request, pk):
    """Return the text and signature fields for a document template as JSON.

    Used by the column-mapping widget on the campaign creation form to
    dynamically build the field-to-CSV-column mapping UI.
    """
    template = get_object_or_404(DocumentTemplate.active, pk=pk, user=value_request.user)
    text_fields = [
        {'field_name': f.field_name, 'label': f.label or f.field_name}
        for f in template.text_fields.all().order_by('id')
    ]
    signature_fields = [
        {'source_column': f.source_column, 'label': f.label or f.source_column}
        for f in template.signature_fields.all().order_by('id')
    ]
    return JsonResponse({'text_fields': text_fields, 'signature_fields': signature_fields})


@login_required
def document_template_background(value_request, pk):
    """Serve a template's background image behind an ownership check instead of
    exposing it at the public /media/ path (B-049), matching the protected
    serving pattern used for signatures and generated documents (B-047)."""
    template = get_object_or_404(DocumentTemplate.active, pk=pk, user=value_request.user)
    file_field = template.template_file
    if not file_field or not file_field.name:
        raise Http404
    content_type = mimetypes.guess_type(file_field.name)[0] or 'application/octet-stream'
    return FileResponse(file_field.open('rb'), content_type=content_type)


@login_required
def document_campaign_create(value_request):
    user = value_request.user

    if not DocumentTemplate.active.filter(user=user).exists():
        messages.warning(value_request, "Please create at least one Document Template first.")
        return redirect('documents:create')
    if not SMTPConfig.active.filter(user=user).exists():
        messages.warning(value_request, "Please configure your SMTP config first.")
        return redirect('accounts:smtp_create')

    if value_request.method == 'POST':
        form = DocumentCampaignForm(value_request.POST, value_request.FILES, user=user)
        if form.is_valid():
            doc_template = form.cleaned_data['document_template']
            auto_send = form.cleaned_data['auto_send']
            # Parse column mapping submitted by the JS widget. Falls back to
            # convention-based resolution (field_name == CSV header) if absent.
            import json as _json
            raw_mapping = value_request.POST.get('column_mapping', '{}')
            try:
                column_mapping = _json.loads(raw_mapping)
                if not isinstance(column_mapping, dict):
                    column_mapping = {}
            except (ValueError, TypeError):
                column_mapping = {}

            campaign = EmailCampaign.objects.create(
                user=user,
                name=form.cleaned_data['campaign_name'],
                template=form.cleaned_data['email_template'],
                smtp_config=form.cleaned_data['smtp_config'],
                document_template=doc_template,
                status='draft',
                is_document_campaign=True,
                column_mapping=column_mapping,
            )
            try:
                recipients_data = parse_csv_recipients(value_request.FILES['csv_file'])
            except Exception as e:
                messages.error(value_request, f"Error parsing CSV file: {e}")
                campaign.delete()
                return redirect('documents:campaign_create')

            if not recipients_data:
                messages.warning(value_request, "CSV did not contain any valid email recipients.")
                campaign.delete()
                return redirect('documents:campaign_create')

            Recipient.objects.bulk_create([
                Recipient(campaign=campaign, email=r['email'], name=r['name'],
                          context_data=r['context'], status='pending')
                for r in recipients_data
            ])
            campaign.total_emails = campaign.recipients.count()
            campaign.save()

            next_run_at = form.apply_schedule(campaign)
            if next_run_at:
                # Beat (trigger_scheduled_campaigns) will generate the documents
                # and send them when the schedule fires.
                from django.utils import timezone
                campaign.status = 'scheduled'
                campaign.save()
                messages.success(
                    value_request,
                    f"Document campaign scheduled: {describe_schedule(campaign)} "
                    f"(first run {timezone.localtime(next_run_at).strftime('%b %d, %Y %H:%M')}). "
                    "Documents will be generated and sent automatically.",
                )
            else:
                generate_documents_task.delay(campaign.id, doc_template.id, auto_send)
                if auto_send:
                    messages.success(value_request, "Document campaign created. Documents are generating and emails will send automatically.")
                else:
                    messages.success(value_request, "Document campaign created. Documents are generating in the background.")
            return redirect('emails:campaign_detail', pk=campaign.id)
    else:
        form = DocumentCampaignForm(user=user)
    return render(value_request, 'documents/document_campaign_form.html', {'form': form})


# ---------------------------------------------------------------------------
# Signature library
# ---------------------------------------------------------------------------

@login_required
def signature_list(value_request):
    signatures = Signature.active.filter(user=value_request.user).order_by('-created_at')
    return render(value_request, 'documents/signature_list.html', {'signatures': signatures})


@login_required
def signature_create(value_request):
    if value_request.method == 'POST':
        form = SignatureForm(value_request.POST, value_request.FILES, user=value_request.user)
        if form.is_valid():
            signature = form.save(commit=False)
            signature.user = value_request.user
            signature.processing_status = Signature.STATUS_PENDING
            signature.save()
            process_signature_task.delay(signature.id)
            messages.success(value_request, "Signature uploaded. Background removal is running in the background.")
            return redirect('documents:signature_detail', pk=signature.id)
    else:
        form = SignatureForm(user=value_request.user)
    return render(value_request, 'documents/signature_form.html', {
        'form': form, 'title': 'Upload Signature',
    })


@login_required
def signature_detail(value_request, pk):
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    return render(value_request, 'documents/signature_detail.html', {'signature': signature})


# Whitelisted signature image variants -> model field. Keeps the serving view
# from being coerced into returning an arbitrary attribute.
_SIGNATURE_VARIANTS = {'original': 'original_image', 'processed': 'processed_image'}


@login_required
def signature_image(value_request, pk, variant):
    """Serve a signature image behind an ownership check instead of exposing it at
    a public/guessable media URL (B-047). Only the owner can fetch their image."""
    field_name = _SIGNATURE_VARIANTS.get(variant)
    if field_name is None:
        raise Http404
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    file_field = getattr(signature, field_name)
    if not file_field or not file_field.name:
        raise Http404
    content_type = mimetypes.guess_type(file_field.name)[0] or 'application/octet-stream'
    return FileResponse(file_field.open('rb'), content_type=content_type)


@login_required
def signature_reprocess(value_request, pk):
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    if value_request.method == 'POST':
        signature.processing_status = Signature.STATUS_PENDING
        signature.error_message = ''
        signature.save(update_fields=['processing_status', 'error_message', 'updated_at'])
        process_signature_task.delay(signature.id)
        messages.success(value_request, "Re-processing started.")
    return redirect('documents:signature_detail', pk=signature.id)


@login_required
def signature_edit(value_request, pk):
    """Interactive touch-up editor: crop, rotate and erase in the browser."""
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    if not signature.is_ready:
        messages.info(value_request, "Wait for background removal to finish before editing.")
        return redirect('documents:signature_detail', pk=signature.id)
    return render(value_request, 'documents/signature_edit.html', {'signature': signature})


@login_required
def signature_save_edit(value_request, pk):
    """Persist a browser-edited (cropped/rotated/erased) signature PNG."""
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    if value_request.method != 'POST':
        return redirect('documents:signature_edit', pk=signature.id)

    data_url = value_request.POST.get('image_data', '')
    prefix = 'data:image/png;base64,'
    if not data_url.startswith(prefix):
        messages.error(value_request, "Could not read the edited image.")
        return redirect('documents:signature_edit', pk=signature.id)
    try:
        raw = base64.b64decode(data_url[len(prefix):])
    except Exception:
        messages.error(value_request, "The edited image was invalid.")
        return redirect('documents:signature_edit', pk=signature.id)
    # Manual edits are small; reject oversized payloads defensively.
    if not raw or len(raw) > 8 * 1024 * 1024:
        messages.error(value_request, "Edited image is empty or too large.")
        return redirect('documents:signature_edit', pk=signature.id)

    signature.processed_image.save(
        f"signature_{signature.id}_edited.png", ContentFile(raw), save=False,
    )
    signature.processing_status = Signature.STATUS_READY
    signature.error_message = ''
    signature.save(update_fields=['processed_image', 'processing_status', 'error_message', 'updated_at'])
    messages.success(value_request, "Signature updated.")
    return redirect('documents:signature_detail', pk=signature.id)


@login_required
def signature_delete(value_request, pk):
    signature = get_object_or_404(Signature.active, pk=pk, user=value_request.user)
    if value_request.method == 'POST':
        # A signature is sensitive, so drop the image files immediately rather
        # than retaining them for the 90-day purge window. Clearing the file
        # fields lets the centralized pre_save cleanup remove them from storage
        # on commit; the soft-deleted row is kept for name-history/audit (B-047).
        signature.original_image = ''
        signature.processed_image = None
        signature.deleted_at = timezone.now()
        signature.save(update_fields=['original_image', 'processed_image', 'deleted_at'])
        messages.success(value_request, "Signature deleted.")
        return redirect('documents:signature_list')
    return redirect('documents:signature_detail', pk=signature.id)
