# SendGlob — Full Implementation & Refactor Plan

**Project:** EmailOPS → SendGlob  
**Date:** 2026-06-07  
**Scope:** Rename, Document Generation Generalization, Scheduling, Data Purging

> **Billing is handled in a separate dedicated plan.** See `docs/billing_implementation_plan.md` for the complete Razorpay integration, credit system, subscription management, and feature gating implementation.

---

## Executive Summary

This plan transforms EmailOPS into SendGlob — a production-ready platform for personalized bulk document generation and delivery. The work is organized into four sequential phases. Each phase is independently deployable. No phase breaks the previous one.

---

## Phase 0 — Project Rename (EmailOPS → SendGlob)

**Goal:** Replace all user-facing "EmailOPS" branding with "SendGlob" without changing internal Django module structure.

> Internal Python packages (`core/`, `emails/`, `certificates/`, etc.) keep their names. Only branding changes.

### Files to Change

| File | Change |
|---|---|
| `pyproject.toml` | `name = "emailops"` → `name = "sendglob"` |
| `core/settings.py` | Add `APP_NAME = "SendGlob"` constant |
| `templates/base.html` (and layout partials) | Replace "EmailOPS" in `<title>`, navbar, footer |
| `templates/emails/` (system-sent emails) | Update sender name and footer branding |
| `core/admin.py` | `admin.site.site_header`, `site_title`, `index_title` |
| `templates/account/` (allauth pages) | Title and branding strings |
| `README.md` | Update project name |
| `CLAUDE.md` | Update project name references |
| Static assets | Replace logo/favicon if they contain "EmailOPS" text |

### Context Processor Addition

Add to `core/context_processors.py`:

```python
def app_branding(request):
    return {"APP_NAME": "SendGlob", "APP_TAGLINE": "Generate. Personalize. Deliver."}
```

Register in `settings.py` `TEMPLATES[0]['OPTIONS']['context_processors']`. Use `{{ APP_NAME }}` in all templates instead of hardcoded strings.

### Effort: 0.5 days | Risk: Low

---

## Phase 1 — Generalize Document Generation (`certificates/` → `documents/`)

**Goal:** Replace the single-field `CertificateTemplate` (name only, fixed coordinates) with a multi-field `DocumentTemplate` that supports any number of text fields at any coordinates, mapped from CSV columns. Works for certificates, offer letters, ID cards, participation letters, marksheets — any image or PDF template.

### 1.1 New App Structure

**Option A (preferred):** Rename `certificates/` → `documents/` at the filesystem level, update `AppConfig`, update `INSTALLED_APPS`, write a data migration.

**Option B:** Keep `certificates/` as a thin wrapper that imports from new `documents/` models. More migration safety.

**Use Option A.** Rationale: cleaner, the old model has no production users yet, migration is straightforward.

### 1.2 New Models

**`documents/models.py`**

```python
class DocumentTemplate(SoftDeleteModel):
    """
    A reusable template for generating personalized documents.
    Supports image (PNG/JPG) or PDF as the background.
    Multiple text fields can be placed at arbitrary coordinates.
    """
    OUTPUT_FORMAT_IMAGE = 'image'
    OUTPUT_FORMAT_PDF = 'pdf'
    OUTPUT_FORMAT_CHOICES = [
        (OUTPUT_FORMAT_IMAGE, 'Image (PNG)'),
        (OUTPUT_FORMAT_PDF, 'PDF'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='document_templates')
    name = models.CharField(max_length=150)
    template_file = models.FileField(
        upload_to='document_templates/',
        help_text="Upload PNG, JPG, or PDF background template."
    )
    output_format = models.CharField(
        max_length=10, choices=OUTPUT_FORMAT_CHOICES, default=OUTPUT_FORMAT_IMAGE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class DocumentTextField(models.Model):
    """
    A single text field to be rendered onto a DocumentTemplate.
    Maps to a CSV column by field_name.
    Supports centered text between x_start/x_end, or left-aligned at x_start.
    """
    ALIGN_CENTER = 'center'
    ALIGN_LEFT = 'left'
    ALIGN_RIGHT = 'right'
    ALIGNMENT_CHOICES = [
        (ALIGN_CENTER, 'Center'),
        (ALIGN_LEFT, 'Left'),
        (ALIGN_RIGHT, 'Right'),
    ]

    template = models.ForeignKey(DocumentTemplate, on_delete=models.CASCADE,
                                 related_name='text_fields')
    field_name = models.SlugField(
        max_length=100,
        help_text="CSV column header this field reads from (e.g. 'participant_name', 'course_title')."
    )
    label = models.CharField(max_length=100, help_text="Human-readable label shown in the UI.")
    x_start = models.IntegerField(help_text="Left boundary X coordinate (pixels).")
    x_end = models.IntegerField(help_text="Right boundary X coordinate (pixels).")
    y_coordinate = models.IntegerField(help_text="Y coordinate for text baseline (pixels).")
    font_size = models.IntegerField(default=48)
    font_color = models.CharField(max_length=50, default="#000000")
    font_file = models.FileField(
        upload_to='document_fonts/', null=True, blank=True,
        help_text="Optional custom TTF font. Falls back to default if blank."
    )
    alignment = models.CharField(max_length=10, choices=ALIGNMENT_CHOICES, default=ALIGN_CENTER)
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Rendering order (lower = rendered first, below other fields)."
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.template.name} — {self.label}"
```

### 1.3 Migration from CertificateTemplate

Write a data migration (`documents/migrations/0002_migrate_from_certificates.py`):
- For each `CertificateTemplate`, create one `DocumentTemplate` + one `DocumentTextField` (field_name=`'name'`, copies all coordinate/font fields).
- Update all `EmailCampaign` FK references from `certificate_template` → `document_template`.

### 1.4 EmailCampaign Model Changes

In `emails/models.py`:

```python
# Replace:
is_certificate_campaign = models.BooleanField(default=False)
certificate_template = models.ForeignKey('certificates.CertificateTemplate', ...)

# With:
is_document_campaign = models.BooleanField(default=False)
document_template = models.ForeignKey(
    'documents.DocumentTemplate',
    on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name='campaigns'
)
```

### 1.5 Recipient Model Changes

```python
# Rename field (with migration):
document_file = models.FileField(
    upload_to='generated_documents/%Y/%m/%d/',
    null=True, blank=True
)
# Remove: certificate_file
```

### 1.6 Generation Task Refactor

Refactor `certificates/tasks.py` → `documents/tasks.py`:

```python
def generate_document_for_recipient(recipient, document_template):
    """
    Renders all DocumentTextField entries onto the template image/PDF.
    Each field reads its value from recipient.context_data[field.field_name].
    Falls back to empty string if column not present (never crash generation).
    """
    # Open template image (Pillow) or PDF page (pypdf / reportlab for PDF)
    # For each text_field in document_template.text_fields.order_by('order'):
    #   value = recipient.context_data.get(text_field.field_name, '')
    #   render centered between x_start/x_end at y_coordinate
    # Save to recipient.document_file
```

**PDF support note:** Use `reportlab` + `pypdf` for PDF template support. Add to `pyproject.toml`.

### 1.7 CSV Column Mapping UI

When a user selects a `DocumentTemplate` for a campaign and uploads a CSV:
- Parse CSV headers.
- Show a mapping interface: for each `DocumentTextField.field_name`, show a dropdown of CSV column headers.
- Store mapping in `EmailCampaign.column_mapping = JSONField(default=dict)`.
- At generation time, use `column_mapping` to extract values from `recipient.context_data`.

### 1.8 Files to Create/Modify

| Action | File |
|---|---|
| Create | `documents/` app (models, views, urls, tasks, admin, apps, migrations) |
| Modify | `emails/models.py` — FK swap |
| Modify | `emails/tasks.py` — import from `documents.tasks` |
| Modify | `core/settings.py` — swap `certificates` for `documents` in INSTALLED_APPS |
| Create | `documents/migrations/0002_migrate_from_certificates.py` |
| Delete | `certificates/` app (after migration verified) |
| Modify | All templates referencing certificate → document |
| Modify | `pyproject.toml` — add `reportlab>=4.0.0`, `pypdf>=4.0.0` |

### Effort: 3–4 days | Risk: Medium (data migration, generation logic refactor)

---

## Phase 2 — Scheduling Feature

**Goal:** Allow campaigns to be sent at a future datetime, with optional recurrence (daily, weekly, monthly). Recurring campaigns auto-clone after completion.

### 2.1 Campaign State Addition

Add `scheduled` to `STATUS_CHOICES` in `EmailCampaign`:

```python
STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('scheduled', 'Scheduled'),   # NEW
    ('queued', 'Queued'),
    ('sending', 'Sending'),
    ('completed', 'Completed'),
    ('failed', 'Failed'),
    ('cancelled', 'Cancelled'),
]
```

### 2.2 New Campaign Fields

```python
# In EmailCampaign:

RECURRENCE_NONE = 'none'
RECURRENCE_DAILY = 'daily'
RECURRENCE_WEEKLY = 'weekly'
RECURRENCE_MONTHLY = 'monthly'
RECURRENCE_CHOICES = [
    (RECURRENCE_NONE, 'One-time'),
    (RECURRENCE_DAILY, 'Daily'),
    (RECURRENCE_WEEKLY, 'Weekly'),
    (RECURRENCE_MONTHLY, 'Monthly'),
]

scheduled_at = models.DateTimeField(
    null=True, blank=True,
    help_text="When to start sending. Null means send immediately when queued."
)
recurrence = models.CharField(
    max_length=10, choices=RECURRENCE_CHOICES, default=RECURRENCE_NONE
)
next_run_at = models.DateTimeField(
    null=True, blank=True,
    help_text="Computed by system. Next time this recurring campaign fires."
)
parent_campaign = models.ForeignKey(
    'self', on_delete=models.SET_NULL,
    null=True, blank=True, related_name='recurrence_children',
    help_text="For recurring campaigns: links recurrence instances to the original."
)
```

### 2.3 State Transition Rules

| Transition | Condition |
|---|---|
| `draft` → `scheduled` | `scheduled_at` is set and is in the future |
| `draft` → `queued` | No `scheduled_at`, user manually starts |
| `scheduled` → `queued` | Celery Beat fires at `scheduled_at` |
| `scheduled` → `draft` | User cancels schedule (edit allowed) |
| `completed` → `scheduled` | Only for recurring: system auto-creates next instance |

**Lock rule update:** `scheduled` campaigns (like `queued`/`sending`) must be treated as **immutable**. No recipient/template/attachment changes while scheduled.

### 2.4 Celery Beat Task

In `emails/tasks.py`:

```python
@shared_task
def trigger_scheduled_campaigns():
    """
    Runs every minute via Celery Beat.
    Finds campaigns in 'scheduled' state whose scheduled_at has passed.
    Moves them to 'queued' and enqueues the send task.
    """
    now = timezone.now()
    due_campaigns = EmailCampaign.objects.filter(
        status='scheduled',
        scheduled_at__lte=now
    ).select_for_update(skip_locked=True)

    for campaign in due_campaigns:
        campaign.status = 'queued'
        campaign.save(update_fields=['status'])
        send_campaign_emails.delay(campaign.id)
```

Register in `core/celery.py` beat schedule:

```python
'trigger-scheduled-campaigns': {
    'task': 'emails.tasks.trigger_scheduled_campaigns',
    'schedule': crontab(minute='*'),  # every minute
},
```

### 2.5 Recurring Campaign Logic

After a campaign completes, if `recurrence != 'none'`:

```python
def _spawn_next_recurrence(campaign):
    """
    Called at end of send_campaign_emails when campaign completes.
    Creates a clone of the campaign scheduled for the next recurrence window.
    Does NOT copy recipients — user must re-upload or the clone starts empty.
    """
    from dateutil.relativedelta import relativedelta  # python-dateutil

    deltas = {
        'daily': timedelta(days=1),
        'weekly': timedelta(weeks=1),
        'monthly': relativedelta(months=1),
    }
    next_at = campaign.scheduled_at + deltas[campaign.recurrence]

    clone = EmailCampaign.objects.create(
        user=campaign.user,
        name=campaign.name,
        template=campaign.template,
        smtp_config=campaign.smtp_config,
        status='scheduled',
        is_document_campaign=campaign.is_document_campaign,
        document_template=campaign.document_template,
        scheduled_at=next_at,
        recurrence=campaign.recurrence,
        next_run_at=next_at,
        parent_campaign=campaign.parent_campaign or campaign,
    )
    campaign.next_run_at = next_at
    campaign.save(update_fields=['next_run_at'])
    return clone
```

**Design decision:** Recurring clones start empty (no recipients). This is intentional — recipient lists change between cycles. The user uploads fresh recipients each time or we add a "carry recipients" option in a future iteration. Document this clearly in the UI.

### 2.6 UI Changes

- Campaign create/edit form: add "Schedule for later" toggle, datetime picker, recurrence dropdown.
- Dashboard: new "Scheduled" section showing upcoming campaigns with countdown.
- Campaign detail: show `scheduled_at`, recurrence info, link to parent/children.
- Block edit on `scheduled` campaigns (same lock UI as `queued`/`sending`).

### 2.7 Files to Create/Modify

| Action | File |
|---|---|
| Modify | `emails/models.py` — new fields + state |
| Modify | `emails/tasks.py` — `trigger_scheduled_campaigns`, `_spawn_next_recurrence` |
| Modify | `core/celery.py` — beat schedule entry |
| Modify | `emails/views.py` — schedule form handling, lock validation |
| Modify | Templates — schedule UI, dashboard scheduled section |
| Modify | `pyproject.toml` — add `python-dateutil>=2.9.0` |

### Effort: 2–3 days | Risk: Low-Medium

---

## Phase 3 — Production-Grade Data Purging

**Goal:** Permanently delete soft-deleted records after a configurable retention window. Safe, dependency-aware, audited, and automated.

### 3.1 Retention Configuration

In `core/settings.py`:

```python
# Days to retain soft-deleted records before physical purge.
# Set higher in production if you need longer audit trails.
SOFT_DELETE_PURGE_RETENTION_DAYS = int(os.getenv("PURGE_RETENTION_DAYS", 90))
```

### 3.2 Purge Order (Dependency Graph)

Physical deletion must respect FK constraints. Purge in this order:

```
1. Recipient               (FK → EmailCampaign)
2. CampaignAttachment      (FK → EmailCampaign)
3. DocumentTextField        (FK → DocumentTemplate — no soft delete, cascade OK)
4. EmailCampaign           (FK → EmailTemplate, SMTPConfig, DocumentTemplate)
5. EmailTemplate           (referenced by EmailCampaign)
6. DocumentTemplate        (referenced by EmailCampaign)
7. SMTPConfig              (referenced by EmailCampaign via PROTECT)
```

### 3.3 Purge Task

Create `core/tasks.py`:

```python
import logging
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def purge_soft_deleted_records():
    """
    Weekly task. Physically deletes soft-deleted records older than
    SOFT_DELETE_PURGE_RETENTION_DAYS. Runs in dependency order.
    Records referenced by active or completed campaigns are skipped.
    """
    cutoff = timezone.now() - timedelta(days=settings.SOFT_DELETE_PURGE_RETENTION_DAYS)
    total_purged = {}

    # Step 1: Recipients (soft delete not implemented — hard delete safe via cascade,
    # but orphaned recipients from purged campaigns are handled in step 3)
    # Recipients do not have soft delete; they are cascade-deleted when their
    # campaign is purged. Skip direct recipient purge.

    # Step 2: Purge campaigns eligible for hard delete
    #   Eligible: deleted_at < cutoff AND status in (completed, failed, cancelled)
    #   NOT eligible: status in (queued, sending, scheduled) — never purge active work
    from emails.models import EmailCampaign, EmailTemplate
    from documents.models import DocumentTemplate
    from accounts.models import SMTPConfig

    safe_statuses = {'completed', 'failed', 'cancelled'}

    campaigns_to_purge = EmailCampaign.objects.filter(
        deleted_at__lt=cutoff,
        status__in=safe_statuses,
    )
    count = 0
    for campaign in campaigns_to_purge:
        logger.info(f"[PURGE] Hard-deleting campaign ID={campaign.pk} name='{campaign.name}'")
        campaign.hard_delete()
        count += 1
    total_purged['campaigns'] = count

    # Step 3: Purge email templates not referenced by any campaign
    templates_to_purge = EmailTemplate.objects.filter(deleted_at__lt=cutoff)
    count = 0
    for tmpl in templates_to_purge:
        if not tmpl.campaigns.exists():
            logger.info(f"[PURGE] Hard-deleting EmailTemplate ID={tmpl.pk}")
            tmpl.hard_delete()
            count += 1
        else:
            logger.warning(
                f"[PURGE] Skipping EmailTemplate ID={tmpl.pk} — still referenced by campaigns."
            )
    total_purged['email_templates'] = count

    # Step 4: Purge document templates not referenced by any campaign
    doc_templates_to_purge = DocumentTemplate.objects.filter(deleted_at__lt=cutoff)
    count = 0
    for tmpl in doc_templates_to_purge:
        if not tmpl.campaigns.exists():
            logger.info(f"[PURGE] Hard-deleting DocumentTemplate ID={tmpl.pk}")
            tmpl.hard_delete()
            count += 1
        else:
            logger.warning(
                f"[PURGE] Skipping DocumentTemplate ID={tmpl.pk} — still referenced by campaigns."
            )
    total_purged['document_templates'] = count

    # Step 5: Purge SMTP configs not referenced by any campaign
    smtp_to_purge = SMTPConfig.objects.filter(deleted_at__lt=cutoff)
    count = 0
    for smtp in smtp_to_purge:
        if not smtp.campaigns.exists():
            logger.info(f"[PURGE] Hard-deleting SMTPConfig ID={smtp.pk}")
            smtp.hard_delete()
            count += 1
        else:
            logger.warning(
                f"[PURGE] Skipping SMTPConfig ID={smtp.pk} — still referenced by campaigns."
            )
    total_purged['smtp_configs'] = count

    logger.info(f"[PURGE] Completed. Summary: {total_purged}")
    return total_purged
```

### 3.4 Beat Schedule Entry

```python
'purge-soft-deleted-records': {
    'task': 'core.tasks.purge_soft_deleted_records',
    'schedule': crontab(hour=3, minute=0, day_of_week=0),  # Weekly, Sunday 3am
},
```

### 3.5 Admin Visibility

Add a read-only admin panel section showing:
- Records soft-deleted and pending purge (with days remaining until purge).
- Last purge run timestamp + summary (store in a `PurgeLog` model or cache).

```python
class PurgeLog(models.Model):
    """Lightweight audit trail for purge runs."""
    ran_at = models.DateTimeField(auto_now_add=True)
    campaigns_purged = models.IntegerField(default=0)
    email_templates_purged = models.IntegerField(default=0)
    document_templates_purged = models.IntegerField(default=0)
    smtp_configs_purged = models.IntegerField(default=0)

    class Meta:
        ordering = ['-ran_at']
```

### 3.6 Files to Create/Modify

| Action | File |
|---|---|
| Create | `core/tasks.py` |
| Create | `core/migrations/000X_purge_log.py` |
| Modify | `core/models.py` — add `PurgeLog` |
| Modify | `core/celery.py` — beat schedule entry |
| Modify | `core/settings.py` — `SOFT_DELETE_PURGE_RETENTION_DAYS` |
| Modify | `core/admin.py` — PurgeLog admin |

### Effort: 1 day | Risk: Low

---

## Phase 4 — UI/UX Consolidation

**Goal:** Update all UI to reflect SendGlob branding, document campaign flows, and scheduling UI.

### Key UI Changes

- **Dashboard:** Add "Scheduled" campaign section.
- **Campaign create flow:** Add "Document Campaign" path (vs "Email only"). Schedule toggle. CSV column mapping for document fields.
- **Document templates:** New section replacing Certificates. Visual field placement UI (future: canvas-based preview).
- **Navigation:** Rename "Certificates" → "Documents" in nav.
- **Error pages:** Rebrand to SendGlob.

### Effort: 2–3 days | Risk: Low

---

## Execution Order & Dependencies

```
Phase 0 (Rename)           → No dependencies. Do first, cleanest starting point.
Phase 1 (Documents)        → After Phase 0. Touches core models.
Phase 2 (Scheduling)       → After Phase 1 (EmailCampaign model settled).
Phase 3 (Purging)          → After Phase 1 (document model names finalized).
Phase 4 (UI)               → After all above. Ties everything together.
Billing                    → See docs/billing_implementation_plan.md. Can begin after Phase 0.
```

**Total estimated effort:** 8–11 days of focused development.

---

## Migration Strategy

1. Back up `db.sqlite3` before starting Phase 1.
2. Run `makemigrations` after every model change, before moving to the next phase.
3. Data migration for CertificateTemplate → DocumentTemplate must be written manually (not auto-generated).
4. Test all migrations on a copy of production data before deploying.

---

## Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Data migration breaks existing campaigns | High | Back up DB; write reverse migration; test on copy |
| Recurring campaign infinite loop | Low | `parent_campaign` FK prevents circular references; max recurrence depth not needed |
| PDF generation performance | Medium | Run in Celery (already async); benchmark with 500-page batch |
| Purge deletes record still in active use | Medium | Status check + FK reference check before hard_delete; `skip_locked` on active campaigns |

---

## Newly Discovered Technical Debt

- `SMTPConfig` currently uses `on_delete=models.PROTECT` in `EmailCampaign`. When a purge task tries to delete an SMTPConfig still referenced by a completed (soft-deleted) campaign, it will raise `ProtectedError`. The purge task must check and handle this explicitly.
- `CampaignAttachment` has no soft delete. If the campaign is purged, attachments cascade-delete (correct behavior, but file cleanup signals must handle this path).

---

*Plan authored: 2026-06-07. Review before each phase begins.*
