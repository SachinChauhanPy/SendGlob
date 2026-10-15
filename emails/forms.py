from django import forms
from django.conf import settings
from emails.models import EmailTemplate, EmailCampaign, CampaignAttachment
from emails import email_designs
from accounts.models import SMTPConfig
from emails.scheduling import compute_next_run
from django.forms.forms import DeclarativeFieldsMetaclass


# Structured content fields shared across layouts (the builder shows the
# relevant subset per layout via JS; the renderer ignores unused keys).
CONTENT_FIELDS = [
    'preheader', 'heading', 'subheading', 'body',
    'button_text', 'button_url', 'event_details', 'image_url', 'footer',
]


class ScheduleFormMixin(metaclass=DeclarativeFieldsMetaclass):
    """Three-mode schedule fields and validation, shared by campaign forms.

    Works with both ModelForm and plain Form. The schedule fields are declared
    here but are NOT listed in any Meta.fields, so the owning view calls
    ``apply_schedule(campaign)`` to persist them and set ``next_run_at``.
    ``clean()`` validates the per-mode requirements and stores the computed
    ``next_run_at`` in ``cleaned_data['next_run_at']``.
    """

    schedule_type = forms.ChoiceField(
        required=False,
        choices=EmailCampaign.SCHEDULE_TYPE_CHOICES,
        initial=EmailCampaign.SCHEDULE_NONE,
        widget=forms.Select(attrs={'class': 'form-select', 'data-schedule-type': '1'}),
        label="Schedule",
        help_text="How this campaign is sent.",
    )
    schedule_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'),
        input_formats=['%H:%M', '%H:%M:%S'],
        label="Time of day (UTC)",
        help_text="Required for any schedule.",
    )
    is_recurring = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label="Repeat on this schedule",
    )
    interval_days = forms.IntegerField(
        required=False, min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        label="Every N days",
    )
    day_of_month = forms.IntegerField(
        required=False, min_value=1, max_value=31,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 31}),
        label="Day of month",
    )
    day_of_week = forms.ChoiceField(
        required=False,
        choices=[('', '---')] + [(str(k), v) for k, v in EmailCampaign.WEEKDAY_CHOICES],
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Day of week",
    )

    SCHEDULE_FIELD_NAMES = (
        'schedule_type', 'schedule_time', 'is_recurring',
        'interval_days', 'day_of_month', 'day_of_week',
    )

    def clean(self):
        cleaned = super().clean()
        stype = cleaned.get('schedule_type') or EmailCampaign.SCHEDULE_NONE
        cleaned['next_run_at'] = None
        if stype == EmailCampaign.SCHEDULE_NONE:
            return cleaned

        if not cleaned.get('schedule_time'):
            self.add_error('schedule_time', "Choose a time of day for the schedule.")
        if stype == EmailCampaign.SCHEDULE_INTERVAL and not cleaned.get('interval_days'):
            self.add_error('interval_days', "Enter the day gap (1 or more).")
        if stype == EmailCampaign.SCHEDULE_MONTHLY and not cleaned.get('day_of_month'):
            self.add_error('day_of_month', "Choose a day of the month (1-31).")
        if stype == EmailCampaign.SCHEDULE_WEEKLY and cleaned.get('day_of_week') in (None, ''):
            self.add_error('day_of_week', "Choose a day of the week.")

        if cleaned.get('is_recurring'):
            user = getattr(self, 'user', None)
            if user:
                from billing import gating
                if not gating.can_use_recurring_scheduling(user):
                    self.add_error(
                        'is_recurring',
                        "Recurring campaign scheduling requires a paid plan with recurring scheduling enabled. "
                        "Upgrade your plan to unlock recurring schedules."
                    )

        if not self.errors:
            nr = compute_next_run(self._schedule_probe(cleaned))
            if nr is None:
                self.add_error('schedule_type', "Could not compute a run time from these settings.")
            cleaned['next_run_at'] = nr
        return cleaned

    def bind_schedule_user(self, user):
        """Record the owner so next-run times compute in their timezone, and
        localize the time-of-day label to that zone (B-038)."""
        self.user = user
        tz = getattr(user, 'timezone', None) or settings.TIME_ZONE or 'UTC'
        self.fields['schedule_time'].label = f"Time of day ({tz})"

    def _schedule_probe(self, cleaned):
        dow = cleaned.get('day_of_week')
        # Carry the owner so the next-run time is computed in the user's
        # timezone (B-038); falls back to UTC when the form has no user.
        return EmailCampaign(
            user=getattr(self, 'user', None),
            schedule_type=cleaned.get('schedule_type') or EmailCampaign.SCHEDULE_NONE,
            schedule_time=cleaned.get('schedule_time'),
            is_recurring=bool(cleaned.get('is_recurring')),
            interval_days=cleaned.get('interval_days'),
            day_of_month=cleaned.get('day_of_month'),
            day_of_week=int(dow) if dow not in (None, '') else None,
        )

    def apply_schedule(self, campaign):
        """Persist validated schedule fields onto ``campaign`` and set
        ``next_run_at``. Returns the next run time (None for manual send)."""
        cd = self.cleaned_data
        stype = cd.get('schedule_type') or EmailCampaign.SCHEDULE_NONE
        campaign.schedule_type = stype
        if stype == EmailCampaign.SCHEDULE_NONE:
            campaign.schedule_time = None
            campaign.is_recurring = False
            campaign.interval_days = None
            campaign.day_of_month = None
            campaign.day_of_week = None
            campaign.next_run_at = None
            return None
        campaign.schedule_time = cd.get('schedule_time')
        campaign.is_recurring = bool(cd.get('is_recurring'))
        campaign.interval_days = cd.get('interval_days') if stype == EmailCampaign.SCHEDULE_INTERVAL else None
        campaign.day_of_month = cd.get('day_of_month') if stype == EmailCampaign.SCHEDULE_MONTHLY else None
        dow = cd.get('day_of_week')
        campaign.day_of_week = int(dow) if (stype == EmailCampaign.SCHEDULE_WEEKLY and dow not in (None, '')) else None
        nr = cd.get('next_run_at') or compute_next_run(campaign)
        campaign.next_run_at = nr
        return nr

    def init_schedule_initial(self, instance):
        """Populate schedule field initials from an existing campaign (edit form)."""
        if not instance or not instance.pk:
            return
        self.fields['schedule_type'].initial = instance.schedule_type
        self.fields['schedule_time'].initial = instance.schedule_time
        self.fields['is_recurring'].initial = instance.is_recurring
        self.fields['interval_days'].initial = instance.interval_days
        self.fields['day_of_month'].initial = instance.day_of_month
        if instance.day_of_week is not None:
            self.fields['day_of_week'].initial = str(instance.day_of_week)


class EmailTemplateForm(forms.ModelForm):
    # --- design / branding ---
    layout = forms.ChoiceField(
        choices=EmailTemplate.LAYOUT_CHOICES,
        widget=forms.RadioSelect, initial='simple', required=True,
    )
    brand_color = forms.CharField(
        required=False, initial='#0078D4',
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color', 'id': 'id_brand_color'}),
    )
    logo_url = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://yourbrand.com/logo.png'}),
    )
    logo_size = forms.IntegerField(
        required=False, initial=40,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 20, 'max': 150, 'placeholder': '40'}),
    )

    # --- structured content ---
    preheader = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': 'Inbox preview text (shown next to the subject)'}))
    heading = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': 'Main headline'}))
    subheading = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': 'Short label / sub-headline'}))
    body = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-control', 'rows': 7, 'placeholder': 'Write your message. Separate paragraphs with a blank line.\nUse {{ name }} to personalize.'}))
    button_text = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': 'e.g. Get started'}))
    button_url = forms.CharField(required=False, widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': 'https://'}))
    event_details = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Date: ...\nTime: ...\nLocation: ...'}))
    image_url = forms.URLField(required=False, widget=forms.URLInput(
        attrs={'class': 'form-control', 'placeholder': 'https://...  (hero image, optional)'}))
    footer = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Why they receive this / unsubscribe line'}))

    # --- advanced: raw custom HTML (used only when layout == custom) ---
    custom_html = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-control font-monospace', 'rows': 14, 'placeholder': '<html>... full custom HTML ...</html>'}))
    custom_text = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-control font-monospace', 'rows': 5, 'placeholder': 'Plain-text fallback'}))

    class Meta:
        model = EmailTemplate
        fields = ['name', 'subject']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Monthly Newsletter'}),
            'subject': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Exciting news inside'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        inst = self.instance
        if inst and inst.pk:
            # Prefill builder fields from saved structured content.
            self.fields['layout'].initial = inst.layout
            self.fields['brand_color'].initial = inst.brand_color or '#0078D4'
            self.fields['logo_url'].initial = inst.logo_url
            self.fields['logo_size'].initial = inst.logo_size or 40
            content = inst.content or {}
            for key in CONTENT_FIELDS:
                if key in content:
                    self.fields[key].initial = content.get(key, '')
            if inst.layout == 'custom':
                self.fields['custom_html'].initial = inst.body_html
                self.fields['custom_text'].initial = inst.body_text

    def collect_content(self):
        """Build the structured content dict from cleaned form data."""
        cd = self.cleaned_data
        return {key: (cd.get(key) or '') for key in CONTENT_FIELDS}

    def save(self, commit=True):
        instance = super().save(commit=False)
        cd = self.cleaned_data
        layout = cd.get('layout') or 'simple'
        brand = cd.get('brand_color') or '#0078D4'
        logo = cd.get('logo_url') or ''
        logo_size = cd.get('logo_size') or 40
        content = self.collect_content()

        instance.layout = layout
        instance.brand_color = brand
        instance.logo_url = logo
        instance.logo_size = logo_size
        instance.content = content

        if layout == 'custom':
            instance.body_html = cd.get('custom_html') or ''
            instance.body_text = cd.get('custom_text') or ''
        else:
            html_out, text_out = email_designs.render(layout, content, brand, logo, logo_size)
            instance.body_html = html_out
            instance.body_text = text_out

        if commit:
            instance.save()
        return instance


class EmailCampaignForm(ScheduleFormMixin, forms.ModelForm):
    # Field to add multiple email addresses quickly
    quick_recipients = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'john@example.com, jane@example.com\n(Or one email per line)'}),
        label="Add Recipients (Single or Multiple)",
        required=False,
        help_text="Enter emails separated by commas, spaces, or newlines."
    )

    csv_file = forms.FileField(
        label="Upload Recipients CSV File",
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
        help_text="Upload a CSV with column headers. Must include 'email' and 'name' columns."
    )

    class Meta:
        model = EmailCampaign
        # Schedule fields come from ScheduleFormMixin and are applied to the
        # campaign by the view via apply_schedule(), not by ModelForm.save().
        fields = ['name', 'template', 'smtp_config']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. May Launch Promotion'}),
            'template': forms.Select(attrs={'class': 'form-select'}),
            'smtp_config': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.bind_schedule_user(user)
        if user:
            # Filter templates and smtp configurations for the logged-in user using active manager
            self.fields['template'].queryset = EmailTemplate.active.filter(user=user)
            self.fields['smtp_config'].queryset = SMTPConfig.active.filter(user=user)

            # If there's an active configuration, pre-select it
            active_smtp = SMTPConfig.active.filter(user=user, is_active=True).first()
            if active_smtp:
                self.fields['smtp_config'].initial = active_smtp


class EmailCampaignEditForm(ScheduleFormMixin, forms.ModelForm):
    quick_recipients = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'john@example.com, jane@example.com\n(Or one email per line)'}),
        label="Add Recipients (Single or Multiple)",
        required=False,
        help_text="Enter emails separated by commas, spaces, or newlines."
    )

    csv_file = forms.FileField(
        label="Upload Recipients CSV File",
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
        help_text="Upload a CSV with column headers. Must include 'email' and 'name' columns."
    )

    recipient_handling = forms.ChoiceField(
        choices=[
            ('append', 'Append new recipients to the existing list'),
            ('replace', 'Replace the entire recipient list with new ones')
        ],
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='append',
        required=False,
        label="Recipient Handling Option"
    )

    class Meta:
        model = EmailCampaign
        fields = ['name', 'template', 'smtp_config', 'document_template']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. May Launch Promotion'}),
            'template': forms.Select(attrs={'class': 'form-select'}),
            'smtp_config': forms.Select(attrs={'class': 'form-select'}),
            'document_template': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.bind_schedule_user(user)
        from documents.models import DocumentTemplate

        # Show the document_template field only for document campaigns.
        if self.instance and self.instance.pk and not self.instance.is_document_campaign:
            if 'document_template' in self.fields:
                del self.fields['document_template']

        if user:
            self.fields['template'].queryset = EmailTemplate.active.filter(user=user)
            self.fields['smtp_config'].queryset = SMTPConfig.active.filter(user=user)
            if 'document_template' in self.fields:
                self.fields['document_template'].queryset = DocumentTemplate.active.filter(user=user)

        # Pre-fill schedule fields from the instance so the edit form shows the
        # current schedule.
        self.init_schedule_initial(self.instance)

        # Lock content and schedule fields once the campaign is no longer editable
        # (locked while queued/sending or inside the scheduled freeze window).
        if self.instance and self.instance.pk and not self.instance.is_editable:
            for field in (['template', 'document_template',
                           'quick_recipients', 'csv_file', 'recipient_handling']
                          + list(self.SCHEDULE_FIELD_NAMES)):
                if field in self.fields:
                    self.fields[field].disabled = True
                    self.fields[field].required = False

    def clean(self):
        cleaned_data = super().clean()
        handling = cleaned_data.get('recipient_handling')
        quick_recipients = cleaned_data.get('quick_recipients')
        csv_file = cleaned_data.get('csv_file')

        if self.instance and self.instance.is_editable:
            if handling == 'replace' and not quick_recipients and not csv_file:
                raise forms.ValidationError(
                    "You selected 'Replace', but provided no new recipients. "
                    "Please add quick recipients or upload a CSV file."
                )
        return cleaned_data
