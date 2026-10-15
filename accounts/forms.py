from zoneinfo import available_timezones

from django import forms
from allauth.account.forms import SignupForm, LoginForm, ResetPasswordForm
from accounts.models import SMTPConfig, CustomUser
from accounts.utils import validate_turnstile


def timezone_choices():
    """Sorted (value, label) pairs of all IANA timezones for the settings form."""
    return [(name, name) for name in sorted(available_timezones())]

class TurnstileSignupForm(SignupForm):
    def clean(self):
        cleaned_data = super().clean()
        req = getattr(self, 'request', None)
        turnstile_token = req.POST.get('cf-turnstile-response') if req else self.data.get('cf-turnstile-response')
        client_ip = req.META.get('REMOTE_ADDR') if req else None
        
        if not validate_turnstile(turnstile_token, remote_ip=client_ip):
            raise forms.ValidationError("CAPTCHA verification failed. Please complete the security check.")
        return cleaned_data


class TurnstileLoginForm(LoginForm):
    def clean(self):
        cleaned_data = super().clean()
        req = getattr(self, 'request', None)
        turnstile_token = req.POST.get('cf-turnstile-response') if req else self.data.get('cf-turnstile-response')
        client_ip = req.META.get('REMOTE_ADDR') if req else None
        
        if not validate_turnstile(turnstile_token, remote_ip=client_ip):
            raise forms.ValidationError("CAPTCHA verification failed. Please complete the security check.")
        return cleaned_data


class TurnstileResetPasswordForm(ResetPasswordForm):
    def clean(self):
        cleaned_data = super().clean()
        req = getattr(self, 'request', None)
        turnstile_token = req.POST.get('cf-turnstile-response') if req else self.data.get('cf-turnstile-response')
        client_ip = req.META.get('REMOTE_ADDR') if req else None
        
        if not validate_turnstile(turnstile_token, remote_ip=client_ip):
            raise forms.ValidationError("CAPTCHA verification failed. Please complete the security check.")
        return cleaned_data


class SMTPConfigForm(forms.ModelForm):
    app_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'App Password or SMTP Password'}),
        label="Password / App Password",
        required=True,
        help_text="For Gmail/Google Workspace and Yahoo, use an App Password from your account's security settings. For other providers, use your SMTP password."
    )

    class Meta:
        model = SMTPConfig
        fields = ['name', 'smtp_host', 'smtp_port', 'smtp_username', 'use_tls', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Work Gmail, Yahoo Mail'}),
            'smtp_host': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. smtp.gmail.com'}),
            'smtp_port': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '587'}),
            'smtp_username': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'you@example.com'}),
            'use_tls': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        is_edit = kwargs.pop('is_edit', False)
        super().__init__(*args, **kwargs)
        if is_edit:
            # If editing, password is not strictly required unless changing it
            self.fields['app_password'].required = False
            self.fields['app_password'].help_text = "Leave blank to keep the existing password."

    def save(self, commit=True):
        instance = super().save(commit=False)
        app_pass = self.cleaned_data.get('app_password')
        if app_pass:
            instance.set_app_password(app_pass)
        if commit:
            instance.save()
        return instance


class UserSettingsForm(forms.ModelForm):
    """Account preferences. Currently the scheduling timezone (B-038)."""

    timezone = forms.ChoiceField(
        choices=timezone_choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Timezone",
        help_text="Campaign schedule times are interpreted in this timezone.",
    )

    class Meta:
        model = CustomUser
        fields = ['timezone']
