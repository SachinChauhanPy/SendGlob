from django import forms

from accounts.utils import validate_turnstile

from .models import ContactMessage


class ContactForm(forms.ModelForm):
    """Public contact form: Turnstile (reused from auth) plus a honeypot field
    as defense in depth. Backend validation is authoritative."""

    # Honeypot: hidden from humans by the template; any value means a bot.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = ContactMessage
        fields = ['name', 'email', 'subject', 'message']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control',
                                           'placeholder': 'Your name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control',
                                             'placeholder': 'you@example.com'}),
            'subject': forms.TextInput(attrs={'class': 'form-control',
                                              'placeholder': 'Subject (optional)'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 6,
                                             'placeholder': 'How can we help?'}),
        }

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('website'):
            # Bots fill every field; humans never see this one. A generic error
            # avoids teaching the bot what tripped it.
            raise forms.ValidationError('Submission could not be processed.')
        req = self.request
        token = req.POST.get('cf-turnstile-response') if req else self.data.get('cf-turnstile-response')
        client_ip = req.META.get('REMOTE_ADDR') if req else None
        if not validate_turnstile(token, remote_ip=client_ip):
            raise forms.ValidationError(
                'CAPTCHA verification failed. Please complete the security check.')
        return cleaned_data
