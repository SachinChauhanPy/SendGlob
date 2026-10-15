from django import forms
from django.forms import inlineformset_factory

from documents.models import (
    DocumentTemplate, DocumentTextField, DocumentSignatureField, Signature,
)
from emails.models import EmailTemplate, EmailCampaign
from emails.forms import ScheduleFormMixin
from accounts.models import SMTPConfig


class SignatureForm(forms.ModelForm):
    """Upload form for a reusable signature. Saving enqueues background removal."""

    class Meta:
        model = Signature
        fields = ['name', 'original_image', 'ink_color_mode', 'ink_color']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Director, or a participant name',
            }),
            'original_image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'ink_color_mode': forms.Select(attrs={'class': 'form-select'}),
            'ink_color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#000000'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['ink_color'].required = False

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError("Please give this signature a name.")
        # Enforce the per-user uniqueness used for CSV resolution, scoped to
        # active (non-deleted) signatures and excluding the current instance.
        qs = Signature.active.filter(user=self.user, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if self.user is not None and qs.exists():
            raise forms.ValidationError("You already have a signature with this name.")
        return name

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('ink_color'):
            cleaned['ink_color'] = '#000000'

        # Validate the image synchronously so bad uploads (photos, documents/ID
        # cards, lined/folded pages) are refused here — before any record is
        # created or background processing starts.
        image = cleaned.get('original_image')
        if image is not None:
            from documents.signature_processing import (
                validate_signature, SignatureProcessingError,
            )
            try:
                image.seek(0)
                raw = image.read()
                image.seek(0)
                validate_signature(raw)
            except SignatureProcessingError as exc:
                self.add_error('original_image', str(exc))
            except Exception:
                self.add_error('original_image',
                               "Could not read this image. Please upload a valid JPG or PNG.")
        return cleaned


class DocumentTemplateForm(forms.ModelForm):
    class Meta:
        model = DocumentTemplate
        fields = [
            'name', 'template_file', 'output_format',
            'use_global_typography', 'global_font_file', 'global_font_size',
            'global_font_color', 'global_alignment',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Workshop Certificate'}),
            'template_file': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'output_format': forms.Select(attrs={'class': 'form-select'}),
            'use_global_typography': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_use_global_typography'}),
            'global_font_file': forms.FileInput(attrs={'class': 'form-control', 'accept': '.ttf'}),
            'global_font_size': forms.NumberInput(attrs={'class': 'form-control'}),
            'global_font_color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#000000'}),
            'global_alignment': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Global typography is only meaningful when the toggle is on; never block
        # saving a template just because these were left at their defaults.
        for name in ('global_font_file', 'global_font_size', 'global_font_color', 'global_alignment'):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        # Backfill model defaults when global fields are blank so non-null model
        # columns are always satisfied.
        if cleaned.get('global_font_size') in (None, ''):
            cleaned['global_font_size'] = 48
        if not cleaned.get('global_font_color'):
            cleaned['global_font_color'] = '#000000'
        if not cleaned.get('global_alignment'):
            cleaned['global_alignment'] = 'center'
        return cleaned


class DocumentTextFieldForm(forms.ModelForm):
    class Meta:
        model = DocumentTextField
        fields = [
            'field_name', 'label', 'x_start', 'x_end', 'y_coordinate',
            'font_size', 'font_color', 'alignment', 'font_file',
        ]
        widgets = {
            'field_name': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'name'}),
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Participant Name'}),
            'x_start': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'x_end': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'y_coordinate': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'font_size': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'font_color': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': '#000000'}),
            'alignment': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'font_file': forms.FileInput(attrs={'class': 'form-control form-control-sm', 'accept': '.ttf'}),
        }


# At least one text field is required so a template always renders something.
DocumentTextFieldFormSet = inlineformset_factory(
    DocumentTemplate, DocumentTextField, form=DocumentTextFieldForm,
    extra=1, min_num=1, validate_min=True, can_delete=True,
)


class DocumentSignatureFieldForm(forms.ModelForm):
    class Meta:
        model = DocumentSignatureField
        fields = [
            'label', 'source_column', 'x_start', 'x_end', 'y_top',
            'max_height', 'alignment',
        ]
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Participant Signature'}),
            'source_column': forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'signature'}),
            'x_start': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'x_end': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'y_top': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'max_height': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'alignment': forms.Select(attrs={'class': 'form-select form-select-sm'}),
        }


# Signature fields are optional: a template can be text-only.
DocumentSignatureFieldFormSet = inlineformset_factory(
    DocumentTemplate, DocumentSignatureField, form=DocumentSignatureFieldForm,
    extra=0, min_num=0, validate_min=False, can_delete=True,
)


class DocumentCampaignForm(ScheduleFormMixin, forms.Form):
    campaign_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Python Workshop Certificates'}),
        label="Campaign Name",
    )
    document_template = forms.ModelChoiceField(
        queryset=DocumentTemplate.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Document Template",
    )
    email_template = forms.ModelChoiceField(
        queryset=EmailTemplate.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Email Notification Template",
    )
    smtp_config = forms.ModelChoiceField(
        queryset=SMTPConfig.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="SMTP Account",
    )
    csv_file = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
        label="Participants CSV File",
        help_text="CSV needs an 'email' column. Each template field reads the CSV column matching its field name.",
    )
    auto_send = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label="Automatically queue and send emails once documents are generated",
        initial=True,
    )
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['document_template'].queryset = DocumentTemplate.active.filter(user=user)
            self.fields['email_template'].queryset = EmailTemplate.active.filter(user=user)
            self.fields['smtp_config'].queryset = SMTPConfig.active.filter(user=user)
            active_smtp = SMTPConfig.active.filter(user=user, is_active=True).first()
            if active_smtp:
                self.fields['smtp_config'].initial = active_smtp

