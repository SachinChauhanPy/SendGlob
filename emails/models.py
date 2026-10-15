import os
from datetime import timedelta
from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils import timezone
from accounts.models import SMTPConfig
import uuid
from core.models import SoftDeleteModel


def generated_document_upload_to(instance, filename):
    """Date-bucketed, randomized path for a generated recipient document.

    The random basename keeps recipient PII out of the stored path and makes the
    file URL unguessable (B-047). Documents are served only through the
    ownership-checked ``recipient_document`` view, not a public media URL."""
    ext = os.path.splitext(filename)[1].lower() or '.bin'
    return f"generated_documents/{timezone.now():%Y/%m/%d}/{uuid.uuid4().hex}{ext}"

class EmailTemplate(SoftDeleteModel):
    LAYOUT_CHOICES = [
        ('simple', 'Simple / Transactional'),
        ('announcement', 'Announcement'),
        ('newsletter', 'Newsletter'),
        ('promotion', 'Promotion / Offer'),
        ('event', 'Event Invite'),
        ('custom', 'Custom HTML'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='templates')
    name = models.CharField(max_length=150)
    subject = models.CharField(max_length=255)
    body_html = models.TextField(help_text="Rendered HTML of the email. Supports variables like {{ name }}, {{ email }}, etc.")
    body_text = models.TextField(blank=True, help_text="Plain text fallback. Supports same variables.")

    # Guided builder fields
    layout = models.CharField(max_length=20, choices=LAYOUT_CHOICES, default='simple',
                              help_text="Design layout used to render this template.")
    brand_color = models.CharField(max_length=7, default='#0078D4',
                                   help_text="Primary brand color (hex) used by the design.")
    logo_url = models.URLField(blank=True, help_text="Optional logo image URL shown in the design.")
    logo_size = models.IntegerField(default=40, help_text="Logo height in pixels.")
    content = models.JSONField(default=dict, blank=True,
                               help_text="Structured content fields (heading, body, button, etc.) for the builder.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class EmailCampaign(SoftDeleteModel):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('queued', 'Queued'),
        ('sending', 'Sending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    # Statuses in which a campaign is "in flight" and must be treated as
    # immutable (no edits to recipients/templates/attachments, no deletion).
    # Single source of truth referenced by views, forms and the SMTP/template
    # protection checks. 'scheduled' is locked because Celery Beat may promote
    # it to 'queued' at any moment.
    LOCKED_STATUSES = ('scheduled', 'queued', 'sending')

    # --- Three-mode scheduling (replaces `recurrence`) ---
    # Every mode requires a time of day. `is_recurring` decides repeat vs one-shot.
    SCHEDULE_NONE = 'none'
    SCHEDULE_INTERVAL = 'interval'   # every N days (first run N days from creation)
    SCHEDULE_MONTHLY = 'monthly'     # one day of the month
    SCHEDULE_WEEKLY = 'weekly'       # one day of the week
    SCHEDULE_TYPE_CHOICES = [
        (SCHEDULE_NONE, 'Send manually'),
        (SCHEDULE_INTERVAL, 'Every N days'),
        (SCHEDULE_MONTHLY, 'Day of month'),
        (SCHEDULE_WEEKLY, 'Day of week'),
    ]
    WEEKDAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=150)
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns')
    smtp_config = models.ForeignKey(SMTPConfig, on_delete=models.PROTECT, related_name='campaigns')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    total_emails = models.IntegerField(default=0)
    sent_emails = models.IntegerField(default=0)
    failed_emails = models.IntegerField(default=0)
    is_document_campaign = models.BooleanField(default=False)
    document_template = models.ForeignKey(
        'documents.DocumentTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campaigns'
    )
    column_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="Maps a DocumentTemplate field_name to the CSV column header for this campaign."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # --- Scheduling (three-mode; see emails/scheduling.py) ---
    next_run_at = models.DateTimeField(
        null=True, blank=True,
        help_text="System-computed time the next recurrence instance fires."
    )
    parent_campaign = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recurrence_children',
        help_text="Links a recurrence instance back to the original campaign."
    )

    # Three-mode schedule specification. `next_run_at` above is the computed
    # source of truth for when the campaign fires next; these describe the rule.
    schedule_type = models.CharField(
        max_length=10, choices=SCHEDULE_TYPE_CHOICES, default=SCHEDULE_NONE,
        help_text="Scheduling mode. 'none' means send manually."
    )
    schedule_time = models.TimeField(
        null=True, blank=True,
        help_text="Time of day (UTC) the campaign fires. Required for any schedule."
    )
    is_recurring = models.BooleanField(
        default=False,
        help_text="If on, the schedule repeats; otherwise it fires once."
    )
    interval_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Interval mode: gap in days between runs (first run is N days out)."
    )
    day_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Monthly mode: day of month 1-31 (clamped to the last valid day)."
    )
    day_of_week = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=WEEKDAY_CHOICES,
        help_text="Weekly mode: 0=Monday .. 6=Sunday."
    )
    reminder_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the pre-run reminder email was sent (idempotency guard)."
    )

    def __str__(self):
        return self.name

    # Statuses that are immutable regardless of timing.
    HARD_LOCKED_STATUSES = ('queued', 'sending')

    @classmethod
    def locked_filter(cls, now=None):
        """Q selecting campaigns that are immutable right now: queued/sending
        always, plus scheduled campaigns inside the pre-run edit-freeze window.
        Single source of truth for SMTP/template protection queries."""
        now = now or timezone.now()
        freeze = now + timedelta(hours=settings.SCHEDULE_EDIT_FREEZE_HOURS)
        return Q(status__in=cls.HARD_LOCKED_STATUSES) | Q(
            status='scheduled', next_run_at__lte=freeze
        )

    def is_within_edit_window(self, now=None):
        """True while a draft (always) or a scheduled campaign that is still more
        than the freeze period away from its next run, so edits are allowed."""
        if self.status == 'draft':
            return True
        if self.status == 'scheduled' and self.next_run_at:
            now = now or timezone.now()
            freeze = timedelta(hours=settings.SCHEDULE_EDIT_FREEZE_HOURS)
            return now < self.next_run_at - freeze
        return False

    @property
    def is_editable(self):
        """Backend-authoritative editability; the UI mirrors it."""
        return self.is_within_edit_window()

    @property
    def is_locked(self):
        """True when the campaign must not be edited or deleted: queued/sending
        always, or scheduled inside the freeze window."""
        if self.status in self.HARD_LOCKED_STATUSES:
            return True
        if self.status == 'scheduled':
            return not self.is_within_edit_window()
        return False

    @property
    def schedule_is_configured(self):
        return self.schedule_type != self.SCHEDULE_NONE and self.schedule_time is not None

    @property
    def schedule_summary(self):
        """Human-readable schedule description for templates."""
        from emails.scheduling import describe_schedule
        return describe_schedule(self)

    @property
    def schedule_timezone(self):
        """IANA name of the owner's scheduling timezone, for display and for the
        ``timezone`` template filter when localizing ``next_run_at``."""
        from emails.scheduling import timezone_label
        return timezone_label(self)

    @property
    def progress_percentage(self):
        if self.total_emails == 0:
            return 0
        return int(((self.sent_emails + self.failed_emails) / self.total_emails) * 100)


class CampaignAttachment(models.Model):
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='campaign_attachments/%Y/%m/%d/')
    filename = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename


class Recipient(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('retrying', 'Retrying'),
    ]

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name='recipients')
    email = models.EmailField()
    name = models.CharField(max_length=150)
    context_data = models.JSONField(default=dict, blank=True, help_text="Dynamic merge tags from CSV")
    document_file = models.FileField(upload_to=generated_document_upload_to, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    tracking_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    # Tracks the last state change; used to detect recipients abandoned in
    # 'sending' by a dead worker so they can be recovered (see emails.tasks).
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} <{self.email}>"


# Physical file cleanup is centralized in core.file_cleanup (single source of
# truth, transaction-safe). See that module for behavior.
from core.file_cleanup import register_file_cleanup

register_file_cleanup(CampaignAttachment, Recipient)
