from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import get_connection, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from accounts.models import SMTPConfig
from emails.models import EmailCampaign
from accounts.forms import SMTPConfigForm, UserSettingsForm
import logging

logger = logging.getLogger(__name__)

@login_required
def account_settings(value_request):
    """Account preferences (scheduling timezone). See B-038."""
    if value_request.method == 'POST':
        form = UserSettingsForm(value_request.POST, instance=value_request.user)
        if form.is_valid():
            form.save()
            messages.success(value_request, "Settings updated.")
            return redirect('accounts:settings')
    else:
        form = UserSettingsForm(instance=value_request.user)
    return render(value_request, 'accounts/settings.html', {'form': form, 'title': 'Settings'})

@login_required
def smtp_config_list(value_request):
    from billing import gating
    configs = SMTPConfig.active.filter(user=value_request.user).order_by('-created_at')
    max_configs = gating.max_smtp_configs(value_request.user)
    can_add = configs.count() < max_configs
    return render(value_request, 'accounts/smtp_config_list.html', {
        'configs': configs,
        'max_configs': max_configs,
        'can_add': can_add,
    })

@login_required
def smtp_config_create(value_request):
    from billing import gating
    max_configs = gating.max_smtp_configs(value_request.user)
    current_count = SMTPConfig.active.filter(user=value_request.user).count()
    if current_count >= max_configs:
        messages.error(
            value_request,
            f"You have reached the maximum number of SMTP configurations ({max_configs}) allowed on your plan. "
            "Please upgrade your plan or remove an existing configuration."
        )
        return redirect('accounts:smtp_list')

    if value_request.method == 'POST':
        form = SMTPConfigForm(value_request.POST)
        if form.is_valid():
            smtp_config = form.save(commit=False)
            smtp_config.user = value_request.user
            smtp_config.save()
            messages.success(value_request, "SMTP configuration created successfully.")
            return redirect('accounts:smtp_list')
    else:
        form = SMTPConfigForm()
    return render(value_request, 'accounts/smtp_config_form.html', {'form': form, 'title': 'Create SMTP Configuration'})

@login_required
def smtp_config_edit(value_request, pk):
    smtp_config = get_object_or_404(SMTPConfig.active, pk=pk, user=value_request.user)
    
    # Block editing if used by active campaigns
    if smtp_config.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot edit this SMTP configuration because it is currently in use by an active campaign.")
        return redirect('accounts:smtp_list')

    if value_request.method == 'POST':
        form = SMTPConfigForm(value_request.POST, instance=smtp_config, is_edit=True)
        if form.is_valid():
            form.save()
            messages.success(value_request, "SMTP configuration updated successfully.")
            return redirect('accounts:smtp_list')
    else:
        form = SMTPConfigForm(instance=smtp_config, is_edit=True)
    return render(value_request, 'accounts/smtp_config_form.html', {'form': form, 'title': 'Edit SMTP Configuration'})

@login_required
def smtp_config_delete(value_request, pk):
    smtp_config = get_object_or_404(SMTPConfig.active, pk=pk, user=value_request.user)
    
    # Block deletion if used by active campaigns
    if smtp_config.campaigns.filter(deleted_at__isnull=True).filter(EmailCampaign.locked_filter()).exists():
        messages.error(value_request, "Cannot delete this SMTP configuration because it is currently in use by an active campaign.")
        return redirect('accounts:smtp_list')

    if value_request.method == 'POST':
        smtp_config.delete()
        messages.success(value_request, "SMTP configuration deleted successfully.")
    return redirect('accounts:smtp_list')

@login_required
def smtp_config_test(value_request, pk):
    smtp_config = get_object_or_404(SMTPConfig.active, pk=pk, user=value_request.user)
    
    if value_request.method == 'POST':
        test_email = value_request.POST.get('test_email')
        if not test_email:
            return JsonResponse({'success': False, 'message': 'Please provide a valid test recipient email.'})
            
        password = smtp_config.get_app_password()
        if not password:
            return JsonResponse({'success': False, 'message': 'Could not decrypt app password. Please re-enter it.'})
            
        try:
            # Create a custom connection using the user's SMTP credentials
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=smtp_config.smtp_host,
                port=smtp_config.smtp_port,
                username=smtp_config.smtp_username,
                password=password,
                use_tls=smtp_config.use_tls,
                timeout=15
            )
            
            tested_at = timezone.now().strftime('%b %d, %Y at %H:%M UTC')
            ctx = {
                'smtp_username': smtp_config.smtp_username,
                'smtp_host': smtp_config.smtp_host,
                'smtp_port': smtp_config.smtp_port,
                'use_tls': smtp_config.use_tls,
                'tested_at': tested_at,
            }
            html_body = render_to_string('emails/smtp_test_email.html', ctx)
            text_body = (
                f"SendGlob — SMTP connection verified\n\n"
                f"Your Gmail SMTP configuration is working correctly.\n\n"
                f"Account : {smtp_config.smtp_username}\n"
                f"Host    : {smtp_config.smtp_host}:{smtp_config.smtp_port}\n"
                f"TLS     : {'Yes' if smtp_config.use_tls else 'No'}\n"
                f"Tested  : {tested_at}\n\n"
                f"You're all set — head to the dashboard to create your first campaign."
            )
            email = EmailMultiAlternatives(
                subject="SendGlob: SMTP connection verified",
                body=text_body,
                from_email=smtp_config.smtp_username,
                to=[test_email],
                connection=connection,
            )
            email.attach_alternative(html_body, 'text/html')
            email.send()
            return JsonResponse({'success': True, 'message': f"SMTP Connection successful! Test email sent to {test_email}."})
        except Exception as e:
            logger.error(f"SMTP Test Connection failed: {e}")
            return JsonResponse({'success': False, 'message': f"SMTP connection failed: {str(e)}"})
            
    return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)
