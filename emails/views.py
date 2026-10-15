from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, FileResponse, Http404
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.views.decorators.csrf import csrf_exempt
from django.template import Template, Context
import csv
import re
import io
import json
import logging
import mimetypes

from emails.models import EmailTemplate, EmailCampaign, Recipient, CampaignAttachment
from emails.forms import EmailTemplateForm, EmailCampaignForm, CONTENT_FIELDS
from emails.tasks import send_campaign_emails_task
from emails.scheduling import describe_schedule
from emails.ai import generate_email_content_ai
from emails import email_designs
from accounts.models import SMTPConfig

logger = logging.getLogger(__name__)

PIXEL_GIF = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\xff\xff\xff\x21\xf9\x04\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x4c\x01\x00\x3b'

@login_required
def recipient_document(value_request, pk):
    """Serve a generated recipient document behind an ownership check instead of a
    public/guessable media URL (B-047). Only the campaign owner can download it."""
    recipient = get_object_or_404(Recipient, pk=pk, campaign__user=value_request.user)
    file_field = recipient.document_file
    if not file_field or not file_field.name:
        raise Http404
    content_type = mimetypes.guess_type(file_field.name)[0] or 'application/octet-stream'
    return FileResponse(file_field.open('rb'), content_type=content_type)


@login_required
def dashboard(value_request):
    user = value_request.user
    campaigns = EmailCampaign.active.filter(user=user).order_by('-created_at')
    
    # Calculate stats
    total_campaigns = campaigns.count()
    total_sent = Recipient.objects.filter(campaign__user=user, campaign__deleted_at__isnull=True, status='sent').count()
    total_failed = Recipient.objects.filter(campaign__user=user, campaign__deleted_at__isnull=True, status='failed').count()
    total_opened = Recipient.objects.filter(campaign__user=user, campaign__deleted_at__isnull=True, opened_at__isnull=False).count()
    
    # Calculate open rate percentage
    open_rate = 0
    if total_sent > 0:
        open_rate = int((total_opened / total_sent) * 100)
        
    recent_campaigns = campaigns[:5]
    scheduled_campaigns = campaigns.filter(status='scheduled').order_by('next_run_at')
    
    # Chart data: last 7 campaigns success/fail
    chart_campaigns = campaigns[:7][::-1] # reverse to show chronological
    chart_labels = [c.name for c in chart_campaigns]
    chart_sent = [c.sent_emails for c in chart_campaigns]
    chart_failed = [c.failed_emails for c in chart_campaigns]

    context = {
        'total_campaigns': total_campaigns,
        'total_sent': total_sent,
        'total_failed': total_failed,
        'total_opened': total_opened,
        'open_rate': open_rate,
        'recent_campaigns': recent_campaigns,
        'scheduled_campaigns': scheduled_campaigns,
        'chart_labels': json.dumps(chart_labels),
        'chart_sent': json.dumps(chart_sent),
        'chart_failed': json.dumps(chart_failed),
    }
    return render(value_request, 'emails/dashboard.html', context)


@login_required
def campaign_list(value_request):
    campaigns = EmailCampaign.active.filter(user=value_request.user).order_by('-created_at')
    return render(value_request, 'emails/campaign_list.html', {'campaigns': campaigns})


@login_required
def campaign_create(value_request):
    # Verify user has at least one SMTP configuration using active manager
    if not SMTPConfig.active.filter(user=value_request.user).exists():
        messages.warning(value_request, "Please set up an SMTP configuration before creating a campaign.")
        return redirect('accounts:smtp_create')

    if value_request.method == 'POST':
        form = EmailCampaignForm(value_request.POST, value_request.FILES, user=value_request.user)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.user = value_request.user
            campaign.save()

            # Upload global attachments
            files = value_request.FILES.getlist('attachments')
            for f in files:
                CampaignAttachment.objects.create(
                    campaign=campaign,
                    file=f,
                    filename=f.name
                )

            # Gather and parse recipients
            recipients_data = []

            # 1. Parse quick recipients
            quick_text = form.cleaned_data.get('quick_recipients', '')
            if quick_text:
                # Regex to split on comma, space, newline, or tab
                emails = re.split(r'[\s,\t\n\r]+', quick_text)
                for email in emails:
                    email = email.strip()
                    if email and '@' in email:
                        name = email.split('@')[0]
                        recipients_data.append({'email': email, 'name': name, 'context': {}})

            # 2. Parse CSV file
            csv_file = value_request.FILES.get('csv_file')
            if csv_file:
                try:
                    csv_recipients = parse_csv_recipients(csv_file)
                    recipients_data.extend(csv_recipients)
                except Exception as csv_err:
                    messages.error(value_request, f"Error reading CSV: {csv_err}")

            # Create all recipients parsed from CSV and quick entries
            recipients_to_create = []
            for r in recipients_data:
                recipients_to_create.append(
                    Recipient(
                        campaign=campaign,
                        email=r['email'],
                        name=r['name'],
                        context_data=r['context'],
                        status='pending'
                    )
                )

            if recipients_to_create:
                from billing import gating
                cap = gating.recipient_cap(value_request.user)
                if cap is not None and len(recipients_to_create) > cap:
                    recipients_to_create = recipients_to_create[:cap]
                    messages.warning(
                        value_request,
                        f"Free tier is limited to {cap} recipients per campaign. "
                        f"Only the first {cap} recipients were added. Upgrade your plan or purchase a credit pack to remove this limit."
                    )
                Recipient.objects.bulk_create(recipients_to_create)
                campaign.total_emails = len(recipients_to_create)
                campaign.save()
                messages.success(value_request, f"Campaign '{campaign.name}' created with {campaign.total_emails} recipients.")
            else:
                messages.warning(value_request, f"Campaign created, but no valid recipients were added.")

            # Apply the schedule chosen on the form. A scheduled campaign needs
            # recipients (it locks against edits inside the freeze window), so a
            # schedule without recipients is saved as a draft instead.
            next_run_at = form.apply_schedule(campaign)
            if next_run_at:
                if campaign.recipients.count() == 0:
                    campaign.status = 'draft'
                    campaign.save()
                    messages.warning(value_request, "Add recipients before scheduling - saved as a draft instead.")
                else:
                    campaign.status = 'scheduled'
                    campaign.save()
                    messages.success(
                        value_request,
                        f"Campaign scheduled: {describe_schedule(campaign)} "
                        f"(first run {timezone.localtime(next_run_at).strftime('%b %d, %Y %H:%M')})."
                    )
            else:
                campaign.save()

            return redirect('emails:campaign_detail', pk=campaign.pk)
    else:
        form = EmailCampaignForm(user=value_request.user)
        
    return render(value_request, 'emails/campaign_form.html', {'form': form})


@login_required
def campaign_detail(value_request, pk):
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
    recipients = campaign.recipients.all().order_by('id')
    
    # Calculate local stats for visual detail
    sent_count = recipients.filter(status='sent').count()
    failed_count = recipients.filter(status='failed').count()
    opened_count = recipients.filter(opened_at__isnull=False).count()

    # Count recipients whose document has not yet been generated (empty FileField).
    docs_pending_count = 0
    if campaign.is_document_campaign:
        docs_pending_count = recipients.filter(document_file='').count()

    context = {
        'campaign': campaign,
        'recipients': recipients,
        'sent_count': sent_count,
        'failed_count': failed_count,
        'opened_count': opened_count,
        'docs_pending_count': docs_pending_count,
    }
    return render(value_request, 'emails/campaign_detail.html', context)


@login_required
def campaign_status_api(value_request, pk):
    """AJAX endpoint for real-time campaign progress polling.

    Returns aggregate stats plus per-recipient status rows so the
    Recipients Log table can be updated without a full page reload.
    """
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)

    agg = campaign.recipients.values('status').annotate(count=Count('status'))
    status_map = {r['status']: r['count'] for r in agg}

    # Lightweight per-recipient snapshot — only the fields the table needs.
    raw_rows = campaign.recipients.values(
        'id', 'status', 'sent_at', 'opened_at', 'retry_count', 'error_message'
    )
    recipient_rows = []
    for r in raw_rows:
        recipient_rows.append({
            'id': r['id'],
            'status': r['status'],
            'retry_count': r['retry_count'],
            'error_message': r['error_message'] or '',
            # Format datetimes to the same strings the template uses so the JS
            # doesn't need to parse and re-format Date objects.
            'sent_at': r['sent_at'].strftime('%b %d, %Y %H:%M') if r['sent_at'] else None,
            'opened_at': r['opened_at'].strftime('%H:%M') if r['opened_at'] else None,
        })

    return JsonResponse({
        'status': campaign.status,
        'total': campaign.total_emails,
        'sent': campaign.sent_emails,
        'failed': campaign.failed_emails,
        'progress': campaign.progress_percentage,
        'breakdown': status_map,
        'recipients': recipient_rows,
    })


@login_required
def campaign_queue(value_request, pk):
    """Queue campaign for sending and trigger task thread."""
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)

    total_recipients = campaign.recipients.count()
    if total_recipients == 0:
        messages.error(value_request, "Cannot start campaign because it has no recipients.")
        return redirect('emails:campaign_detail', pk=campaign.pk)

    from billing import gating
    cap = gating.recipient_cap(value_request.user)
    if cap is not None and total_recipients > cap:
        messages.error(
            value_request,
            f"Free tier is limited to {cap} recipients per campaign ({total_recipients} found). "
            "Please upgrade to a paid plan or purchase a credit pack to send to larger audiences."
        )
        return redirect('emails:campaign_detail', pk=campaign.pk)

    if campaign.status in ['failed', 'cancelled']:
        sent_count = campaign.recipients.filter(status='sent').count()
        if sent_count == total_recipients:
            messages.error(value_request, "All emails in this campaign have already been sent. Use 'Restart Campaign' if you wish to resend.")
            return redirect('emails:campaign_detail', pk=campaign.pk)

    if campaign.status in ['draft', 'scheduled', 'failed', 'cancelled']:
        # Reset recipients from failed or cancelled back to pending
        campaign.recipients.filter(status__in=['failed', 'sending']).update(status='pending')
        campaign.status = 'queued'
        # Starting now overrides any pending schedule.
        campaign.next_run_at = None
        campaign.reminder_sent_at = None
        campaign.sent_emails = campaign.recipients.filter(status='sent').count()
        campaign.failed_emails = campaign.recipients.filter(status='failed').count()
        campaign.total_emails = total_recipients
        campaign.save()

        if campaign.is_document_campaign and campaign.document_template:
            from documents.tasks import generate_documents_task
            generate_documents_task.delay(campaign.id, campaign.document_template.id, True)
            messages.success(value_request, "Campaign queued. Generating documents and starting sender...")
        else:
            send_campaign_emails_task.delay(campaign.id)
            messages.success(value_request, "Campaign queued and background sender started.")
    elif campaign.status in ['queued', 'sending']:
        messages.info(value_request, "Campaign is already being sent.")
    elif campaign.status == 'completed':
        messages.info(value_request, "Campaign has already completed. Use 'Restart Campaign' to resend.")
    return redirect('emails:campaign_detail', pk=campaign.pk)


@login_required
def campaign_cancel(value_request, pk):
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
    if campaign.status in ['queued', 'sending']:
        campaign.status = 'cancelled'
        campaign.save()
        messages.info(value_request, "Campaign sending cancelled.")
    else:
        messages.warning(value_request, f"Campaign cannot be cancelled because it is in '{campaign.status}' status.")
    return redirect('emails:campaign_detail', pk=campaign.pk)


@login_required
def campaign_unschedule(value_request, pk):
    """Cancel a pending schedule, returning the campaign to 'draft' so it can be
    edited again. Only valid while the campaign is in the 'scheduled' state."""
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
    if campaign.status == 'scheduled':
        campaign.status = 'draft'
        campaign.schedule_type = EmailCampaign.SCHEDULE_NONE
        campaign.schedule_time = None
        campaign.is_recurring = False
        campaign.interval_days = None
        campaign.day_of_month = None
        campaign.day_of_week = None
        campaign.next_run_at = None
        campaign.reminder_sent_at = None
        campaign.save(update_fields=[
            'status', 'schedule_type', 'schedule_time', 'is_recurring',
            'interval_days', 'day_of_month', 'day_of_week',
            'next_run_at', 'reminder_sent_at',
        ])
        messages.info(value_request, "Schedule cancelled. Campaign returned to draft.")
    else:
        messages.warning(value_request, f"Campaign is not scheduled (status: {campaign.status}).")
    return redirect('emails:campaign_detail', pk=campaign.pk)


@login_required
def campaign_restart(value_request, pk):
    """Restart campaign from scratch. Resets all stats and recipient logs, then queues the task."""
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
    if campaign.status in ['completed', 'failed', 'cancelled']:
        if value_request.method == 'POST':
            if campaign.recipients.count() == 0:
                messages.error(value_request, "Cannot restart campaign because it has no recipients.")
                return redirect('emails:campaign_detail', pk=campaign.pk)
            from billing import gating
            cap = gating.recipient_cap(value_request.user)
            if cap is not None and campaign.recipients.count() > cap:
                messages.error(
                    value_request,
                    f"Free tier is limited to {cap} recipients per campaign ({campaign.recipients.count()} found). "
                    "Please upgrade or purchase a credit pack to restart."
                )
                return redirect('emails:campaign_detail', pk=campaign.pk)
            recipients = campaign.recipients.all()
            if campaign.is_document_campaign:
                # Delete physical generated document files before resetting.
                for r in recipients:
                    if r.document_file:
                        try:
                            r.document_file.delete(save=False)
                        except Exception as e:
                            logger.error(f"Error deleting document file during restart: {e}")

            recipients.update(
                status='pending',
                sent_at=None,
                opened_at=None,
                error_message=None,
                retry_count=0,
                document_file=None
            )
            campaign.status = 'queued'
            campaign.sent_emails = 0
            campaign.failed_emails = 0
            campaign.total_emails = campaign.recipients.count()
            campaign.started_at = None
            campaign.completed_at = None
            campaign.next_run_at = None
            campaign.reminder_sent_at = None
            campaign.save()

            if campaign.is_document_campaign and campaign.document_template:
                from documents.tasks import generate_documents_task
                generate_documents_task.delay(campaign.id, campaign.document_template.id, True)
                messages.success(value_request, "Campaign restarted. Regenerating documents and sending...")
            else:
                send_campaign_emails_task.delay(campaign.id)
                messages.success(value_request, "Campaign restarted from scratch.")
    return redirect('emails:campaign_detail', pk=campaign.pk)



@login_required
def campaign_delete(value_request, pk):
    campaign = get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
    
    # Block deleting while the campaign is locked (queued/sending, or scheduled
    # inside the pre-run freeze window).
    if campaign.is_locked:
        messages.error(value_request, "Cannot delete a locked campaign. Unschedule it first.")
        return redirect('emails:campaign_detail', pk=campaign.pk)

    if value_request.method == 'POST':
        campaign.delete()
        messages.success(value_request, "Campaign deleted successfully.")
        return redirect('emails:campaign_list')
    return redirect('emails:campaign_detail', pk=campaign.pk)


# Template Views
@login_required
def template_list(value_request):
    templates = EmailTemplate.active.filter(user=value_request.user).order_by('-created_at')
    return render(value_request, 'emails/template_list.html', {
        'templates': templates, 'layouts': email_designs.LAYOUTS,
    })


@login_required
def template_create(value_request):
    if value_request.method == 'POST':
        form = EmailTemplateForm(value_request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.user = value_request.user
            template.save()
            messages.success(value_request, "Template created successfully.")
            return redirect('emails:template_list')
    else:
        # Optionally start from a chosen design (?layout=key) with its defaults.
        layout = value_request.GET.get('layout', 'simple')
        if not email_designs.get_layout(layout):
            layout = 'simple'
        initial = {'layout': layout, 'brand_color': '#0078D4'}
        initial.update(email_designs.default_content(layout))
        if 'subject' not in initial or not initial.get('subject'):
            initial['subject'] = initial.get('heading', '')
        form = EmailTemplateForm(initial=initial)
    return render(value_request, 'emails/template_form.html', {
        'form': form, 'title': 'Create Template', 'layouts': email_designs.LAYOUTS,
        'layouts_fields_json': json.dumps({l['key']: l['fields'] for l in email_designs.LAYOUTS}),
    })


@login_required
def template_edit(value_request, pk):
    template = get_object_or_404(EmailTemplate.active, pk=pk, user=value_request.user)
    
    # Check for active campaigns referencing this template
    if template.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot edit this template because it is currently in use by an active campaign.")
        return redirect('emails:template_list')

    if value_request.method == 'POST':
        form = EmailTemplateForm(value_request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(value_request, "Template updated successfully.")
            return redirect('emails:template_list')
    else:
        form = EmailTemplateForm(instance=template)
    return render(value_request, 'emails/template_form.html', {
        'form': form, 'title': 'Edit Template', 'layouts': email_designs.LAYOUTS,
        'layouts_fields_json': json.dumps({l['key']: l['fields'] for l in email_designs.LAYOUTS}),
    })


@login_required
def template_delete(value_request, pk):
    template = get_object_or_404(EmailTemplate.active, pk=pk, user=value_request.user)
    
    # Check for active campaigns referencing this template
    if template.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot delete this template because it is currently in use by an active campaign.")
        return redirect('emails:template_list')

    if value_request.method == 'POST':
        template.delete()
        messages.success(value_request, "Template deleted successfully.")
    return redirect('emails:template_list')


@login_required
def template_clone(value_request, pk):
    """Duplicate an existing template so the user can reuse the design with new content."""
    original = get_object_or_404(EmailTemplate.active, pk=pk, user=value_request.user)
    if value_request.method != 'POST':
        return redirect('emails:template_list')
    clone = EmailTemplate.objects.create(
        user=value_request.user,
        name=f"{original.name} (copy)",
        subject=original.subject,
        body_html=original.body_html,
        body_text=original.body_text,
        layout=original.layout,
        brand_color=original.brand_color,
        logo_url=original.logo_url,
        logo_size=original.logo_size,
        content=dict(original.content or {}),
    )
    messages.success(value_request, "Template cloned. Edit the copy and replace the content.")
    return redirect('emails:template_edit', pk=clone.pk)


# Sample merge data so the live preview reads like a real email.
_PREVIEW_SAMPLES = {
    'name': 'Alex Morgan',
    'first_name': 'Alex',
    'email': 'alex@example.com',
    'company': 'Acme Corp',
    'date': 'May 26, 2026',
    'event_title': 'Web Development Seminar',
}


def _render_preview_with_django(html):
    """Render the template string with Django's native Template engine to guarantee 100% accuracy."""
    try:
        t = Template(html)
        c = Context(_PREVIEW_SAMPLES)
        return t.render(c)
    except Exception as e:
        err_msg = (
            f'<div style="background-color:#F25022; color:#ffffff; padding:16px; '
            f'font-family:\'Ubuntu\',sans-serif; font-size:14px; font-weight:bold; '
            f'border-bottom:3px solid #d8410f; box-shadow:0 2px 5px rgba(0,0,0,0.1);">'
            f'Template Syntax Error: {e}</div>'
        )
        return err_msg + html


@login_required
def template_preview(value_request):
    """Render the current builder state to live HTML for the preview iframe."""
    if value_request.method != 'POST':
        return HttpResponseBadRequest("POST required")
    layout = value_request.POST.get('layout', 'simple')
    brand = value_request.POST.get('brand_color', '#0078D4')
    logo = value_request.POST.get('logo_url', '')
    logo_size_str = value_request.POST.get('logo_size', '40')
    try:
        logo_size = int(logo_size_str)
    except ValueError:
        logo_size = 40

    if layout == 'custom':
        html = value_request.POST.get('custom_html', '').strip() or (
            '<div style="padding:48px;font-family:Arial,sans-serif;color:#9b9b9b;text-align:center;">'
            'Paste custom HTML to preview it here.</div>'
        )
    else:
        content = {key: value_request.POST.get(key, '') for key in CONTENT_FIELDS}
        html, _ = email_designs.render(layout, content, brand, logo, logo_size=logo_size)

    return HttpResponse(_render_preview_with_django(html), content_type='text/html')


# Open Pixel Tracking View
@csrf_exempt
def email_open_track(value_request, tracking_uuid):
    try:
        recipient = Recipient.objects.get(tracking_uuid=tracking_uuid)
        if not recipient.opened_at:
            recipient.opened_at = timezone.now()
            recipient.save(update_fields=['opened_at'])
    except Recipient.DoesNotExist:
        pass
    
    return HttpResponse(PIXEL_GIF, content_type='image/gif')


# AI Template Helper view
@login_required
def template_generate_ai(value_request):
    """AJAX view: generate structured email copy for the chosen layout."""
    if value_request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=400)

    try:
        data = json.loads(value_request.body)
        prompt = data.get('prompt', '').strip()
        layout = data.get('layout', 'simple')
        brand_color = data.get('brand_color', '#0078D4')
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid request body.'}, status=400)

    if not prompt:
        return JsonResponse({'success': False, 'error': 'Prompt cannot be empty.'}, status=400)

    if not email_designs.get_layout(layout):
        layout = 'simple'

    # Credit metering: AI template generation hits a metered external API, so it
    # costs credits. Gate before calling and debit only on a successful result.
    from billing import credit_costs, wallet as credit_wallet
    if not credit_wallet.can_afford(value_request.user, credit_costs.AI_TEMPLATE_GENERATION):
        return JsonResponse(
            {'success': False, 'error': 'Insufficient credits for AI generation.'}, status=402)

    res = generate_email_content_ai(prompt, layout=layout, brand_color=brand_color)

    if res.get('success', False):
        try:
            credit_wallet.debit(value_request.user, credit_costs.AI_TEMPLATE_GENERATION,
                                transaction_type='debit_ai', note='AI email content')
        except credit_wallet.InsufficientCreditsError:
            return JsonResponse(
                {'success': False, 'error': 'Insufficient credits for AI generation.'}, status=402)
        return JsonResponse(res)
    return JsonResponse({'success': False, 'error': res.get('error', 'AI generation failed.')}, status=500)


def parse_csv_recipients(file_obj):
    """
    Decodes and parses recipients and contextual columns from CSV files.
    """
    # Read and decode csv content handling byte order mark (utf-8-sig)
    content = file_obj.read().decode('utf-8-sig').splitlines()
    reader = csv.DictReader(content)
    
    recipients = []
    headers = reader.fieldnames or []
    
    # Normalize headers
    headers_trimmed = [h.strip() for h in headers]
    
    email_col = None
    name_col = None
    
    # Auto detect columns
    for h in headers_trimmed:
        h_low = h.lower()
        if 'email' in h_low or 'mail' in h_low:
            email_col = h
            break
    for h in headers_trimmed:
        h_low = h.lower()
        if 'name' in h_low or 'username' in h_low or 'participant' in h_low:
            name_col = h
            break
            
    # Fallback mappings if auto detect fails
    if not email_col and len(headers_trimmed) > 0:
        email_col = headers_trimmed[0]
    if not name_col and len(headers_trimmed) > 1:
        name_col = headers_trimmed[1]
    elif not name_col:
        name_col = email_col

    for row in reader:
        # Strip all keys and values in row safely
        clean_row = {}
        for k, v in row.items():
            if k is not None:
                k_clean = str(k).strip()
                v_clean = str(v).strip() if v is not None else ''
                clean_row[k_clean] = v_clean
        
        email = clean_row.get(email_col, '').strip() if email_col else ''
        if not email or '@' not in email:
            continue
            
        name = clean_row.get(name_col, '').strip() if name_col else ''
        if not name:
            name = email.split('@')[0]
            
        # Everything else goes into context mapping
        context = {k: v for k, v in clean_row.items() if k not in (email_col, name_col)}
        
        recipients.append({
            'email': email,
            'name': name,
            'context': context
        })
        
    return recipients


@login_required
def campaign_edit(value_request, pk):
    from django.db import transaction
    from emails.forms import EmailCampaignEditForm

    # Use select_for_update transaction lock to prevent race conditions with background sending
    with transaction.atomic():
        campaign = get_object_or_404(EmailCampaign.active.select_for_update(), pk=pk, user=value_request.user)

        # Block edits while the campaign is locked (queued/sending, or scheduled
        # inside the pre-run freeze window). Editable scheduled campaigns pass.
        if campaign.is_locked:
            messages.error(value_request, "Cannot edit a locked campaign. It is sending or about to run.")
            return redirect('emails:campaign_detail', pk=campaign.pk)

        # Cache initial document template for comparison
        old_doc_template = campaign.document_template

        if value_request.method == 'POST':
            form = EmailCampaignEditForm(value_request.POST, value_request.FILES, instance=campaign, user=value_request.user)
            if form.is_valid():
                campaign = form.save(commit=False)
                campaign.save()

                if campaign.is_editable:
                    # 1. Handle deferred attachment deletions
                    delete_attachment_ids = value_request.POST.getlist('delete_attachments')
                    if delete_attachment_ids:
                        attachments_to_delete = campaign.attachments.filter(id__in=delete_attachment_ids)
                        # Physical files are removed by the centralized post_delete
                        # signal (core.file_cleanup) once the delete commits.
                        for attachment in attachments_to_delete:
                            attachment.delete()

                    # 2. Upload new global attachments
                    files = value_request.FILES.getlist('attachments')
                    for f in files:
                        CampaignAttachment.objects.create(
                            campaign=campaign,
                            file=f,
                            filename=f.name
                        )

                    # 3. Recipient parsing and handling
                    recipients_data = []
                    # Parse quick recipients
                    quick_text = form.cleaned_data.get('quick_recipients', '')
                    if quick_text:
                        emails = re.split(r'[\s,\t\n\r]+', quick_text)
                        for email in emails:
                            email = email.strip()
                            if email and '@' in email:
                                name = email.split('@')[0]
                                recipients_data.append({'email': email, 'name': name, 'context': {}})
                    # Parse CSV file
                    csv_file = value_request.FILES.get('csv_file')
                    if csv_file:
                        try:
                            csv_recipients = parse_csv_recipients(csv_file)
                            recipients_data.extend(csv_recipients)
                        except Exception as csv_err:
                            messages.error(value_request, f"Error reading CSV: {csv_err}")

                    handling = form.cleaned_data.get('recipient_handling', 'append')
                    
                    if handling == 'replace' and (quick_text or csv_file):
                        # Generated document files are removed by the centralized
                        # post_delete signal (core.file_cleanup) for each recipient.
                        recipients_to_delete = campaign.recipients.all()
                        recipients_to_delete.delete()
                        campaign.total_emails = campaign.recipients.count()
                        campaign.save()

                    recipients_to_create = []
                    for r in recipients_data:
                        recipients_to_create.append(
                            Recipient(
                                campaign=campaign,
                                email=r['email'],
                                name=r['name'],
                                context_data=r['context'],
                                status='pending'
                            )
                        )
                    
                    if recipients_to_create:
                        from billing import gating
                        cap = gating.recipient_cap(value_request.user)
                        if cap is not None:
                            existing_count = campaign.recipients.count()
                            remaining_slots = max(0, cap - existing_count)
                            if len(recipients_to_create) > remaining_slots:
                                recipients_to_create = recipients_to_create[:remaining_slots]
                                messages.warning(
                                    value_request,
                                    f"Free tier is limited to {cap} recipients per campaign. "
                                    f"Only {remaining_slots} additional recipients were added. "
                                    "Upgrade or purchase a credit pack to remove this limit."
                                )
                        if recipients_to_create:
                            Recipient.objects.bulk_create(recipients_to_create)
                        campaign.total_emails = campaign.recipients.count()
                        campaign.save()

                    # Handle document regeneration if template changed.
                    new_doc_template = campaign.document_template
                    if campaign.is_document_campaign and old_doc_template != new_doc_template:
                        from documents.tasks import generate_documents_task
                        # B-054: bulk-reset stale documents instead of one
                        # save() per recipient. Bulk update bypasses the
                        # pre_save cleanup signal, so delete the physical files
                        # explicitly first (campaign_restart pattern, B-043).
                        stale = campaign.recipients.exclude(document_file='')
                        for r in stale:
                            try:
                                r.document_file.delete(save=False)
                            except Exception as e:
                                logger.error(f"Error deleting stale document file during campaign edit: {e}")
                        stale.update(document_file=None, status='pending')

                        if new_doc_template:
                            generate_documents_task.delay(campaign.id, new_doc_template.id, False)

                # Apply schedule edits made during the edit window. Only act when
                # the schedule fields were actually submitted (the edit template
                # may omit them) and the campaign is still editable.
                if 'schedule_type' in value_request.POST and campaign.is_editable:
                    next_run_at = form.apply_schedule(campaign)
                    if next_run_at and campaign.recipients.exists():
                        campaign.status = 'scheduled'
                    elif next_run_at:
                        # B-051: a schedule without recipients must never stay
                        # armed - Beat would promote it and produce a spurious
                        # "completed" campaign that sent nothing.
                        campaign.status = 'draft'
                        campaign.next_run_at = None
                        messages.warning(
                            value_request,
                            "Add recipients before scheduling - the campaign was saved as a draft instead.",
                        )
                    elif campaign.status == 'scheduled' and not next_run_at:
                        campaign.status = 'draft'
                    campaign.save()

                messages.success(value_request, "Campaign updated successfully.")
                return redirect('emails:campaign_detail', pk=campaign.pk)
        else:
            form = EmailCampaignEditForm(instance=campaign, user=value_request.user)

    # Attachments for template rendering
    attachments = campaign.attachments.all()
    
    return render(value_request, 'emails/campaign_edit.html', {
        'form': form,
        'campaign': campaign,
        'attachments': attachments,
    })
