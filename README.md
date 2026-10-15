# SendGlob

Generate. Personalize. Deliver.

SendGlob is a self-hosted Django platform for bulk email campaigns and automated
personalized-document generation (certificates, offer letters, ID cards, and
similar). Every user connects their own Gmail SMTP credentials, so mail is sent
from the user's own mailbox rather than a shared relay. Campaigns, recipients,
templates, generated documents, and delivery history are all first-class,
soft-deleted records so historical reporting stays accurate over time.

This README describes features that are implemented in the codebase today.

---

## Feature Overview

### Email campaigns
- Per-user SMTP configuration. Each user stores their own Gmail host, port, and
  App Password. The App Password is encrypted at rest with Fernet
  (`cryptography`) and never stored in plaintext.
- Reusable email templates with a guided builder. Templates support multiple
  layouts (simple/transactional, announcement, newsletter, promotion, event
  invite, custom HTML), a brand color, an optional logo, and merge variables
  such as `{{ name }}` and `{{ email }}`.
- AI-assisted copywriting. An optional Google Gemini integration generates
  structured HTML and plain-text email content for the chosen layout from a short
  prompt. It is inactive until `GEMINI_API_KEY` is set.
- Recipient import from CSV with arbitrary merge columns, in either append or
  replace mode, plus per-campaign file attachments.
- Background delivery with throttling and retries. Sending runs in Celery so HTTP
  requests never block; mail is sent in batches with a throttle delay to respect
  Gmail limits, and failed sends are retried (up to three attempts) with the
  failure reason recorded per recipient.
- A 1x1 tracking pixel records unique opens and open times, surfaced as an open
  rate on the dashboard.

### Scheduling
- Three scheduling modes: every N days, a day of the month, or a day of the week,
  each with a time of day, plus one-shot or recurring.
- Schedule times are interpreted in each user's own timezone and stored in UTC.
- Celery Beat promotes due campaigns to the send queue, and an editing freeze
  window locks a campaign shortly before it runs. Owners receive a pre-run
  reminder email when a run enters that window.

### Document and certificate generation
- Coordinate-driven rendering with Pillow. A document template is a background
  image (PNG/JPG) with any number of text fields placed by pixel coordinates.
  Each field is read from a CSV column and rendered between `x_start` and `x_end`
  at `y_coordinate`, with configurable font, size, color, and alignment, and
  automatic shrink-to-fit so long values stay inside their box.
- Per-template global typography or per-field typography.
- Output as PNG image or PDF, generated per recipient in the background and
  optionally emailed automatically once generated.

### Signatures
- A reusable signature library. Users upload a photo or scan; a background-removal
  pipeline produces a clean transparent PNG. Uploads are validated up front
  (non-signature images are rejected, with a decompression-bomb guard).
- Per-recipient signing. A document template can place a signature field that
  resolves each recipient's signature by name from a CSV column and composites it
  onto the generated document.
- A browser-based touch-up editor (crop, rotate, erase) for the processed image.

### Data lifecycle and integrity
- Soft delete across the important entities (campaigns, templates, SMTP configs,
  document templates, signatures). User-facing queries use an `active` manager;
  the unfiltered default manager exists only for foreign-key integrity,
  historical reporting, and admin.
- A weekly Celery purge physically removes soft-deleted records past a retention
  window in foreign-key-safe order, never touching in-flight or scheduled
  campaigns, and writes an audit row (`PurgeLog`) for every run.
- Centralized, transaction-safe file cleanup keeps physical files in sync with
  the database, plus a `find_orphan_media` command to reconcile storage against
  the database.

### Security
- Per-user SMTP App Passwords encrypted with Fernet.
- Signature images encrypted at rest with a dedicated encrypted storage backend.
- Sensitive files (signature images and generated recipient documents) are served
  only through authenticated, ownership-checked views, never a public media URL,
  and are stored under randomized filenames. See `docs/media-security.md`.
- Cloudflare Turnstile CAPTCHA on login, signup, and password reset (defaults to
  Cloudflare test keys for local development).
- Google sign-in and sign-up via `django-allauth`, auto-linked to an existing
  account with the same verified email.
- Custom 400/403/404/500 error pages and a light/dark, theme-aware UI built on
  Bootstrap 5.

---

## Technology Stack

- Python 3.13, Django 6
- SQLite (default database)
- Celery with a Redis broker; results stored via `django-celery-results`
- Pillow for image and PDF rendering; NumPy and SciPy for the signature pipeline
- `django-allauth` for authentication and Google OAuth
- `cryptography` (Fernet) for credential and file encryption
- Bootstrap 5 with Django templates
- `uv` for dependency management

---

## Project Structure

```
EmailOPS/                       # repository root (product name: SendGlob)
├── pyproject.toml              # uv dependency definitions
├── manage.py
├── core/                       # project config and shared infrastructure
│   ├── settings.py             # settings (auth, Celery, crypto, scheduling)
│   ├── urls.py                 # root routing
│   ├── celery.py               # Celery app and Beat schedule
│   ├── models.py               # SoftDeleteModel base, PurgeLog
│   ├── tasks.py                # weekly soft-delete purge
│   ├── storage.py              # Fernet encrypted-at-rest file storage
│   ├── file_cleanup.py         # transaction-safe physical file cleanup
│   ├── middleware.py           # dynamic CSRF handling
│   ├── context_processors.py   # Turnstile keys, app branding
│   ├── views.py                # custom error pages
│   └── management/commands/    # purge_soft_deleted, find_orphan_media,
│                               #   encrypt_signature_files
├── accounts/                   # auth, users, encrypted SMTP configs
├── emails/                     # templates, campaigns, recipients, scheduling,
│                               #   delivery tasks, tracking, AI copy
├── documents/                  # document templates, rendering, signatures
├── certificates/               # deprecated, retained for migration history only
├── templates/                  # Django templates and UI components
├── static/                     # CSS, JavaScript, fonts
└── docs/                       # architecture, database, and feature docs
```

---

## Setup

### 1. Prerequisites
Install `uv`, and run a Redis server if you want background processing
(scheduling, bulk send, document generation).

### 2. Environment
Copy the example environment file and adjust as needed:
```bash
cp .env.example .env
```
All settings have working defaults for local development, including Turnstile test
keys that always pass. Notable variables:

| Variable | Purpose |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Standard Django settings |
| `SMTP_ENCRYPTION_KEY` | Fernet key for encrypting SMTP App Passwords |
| `FILE_ENCRYPTION_KEY` | Fernet key for encrypting signature files at rest (defaults to `SMTP_ENCRYPTION_KEY`) |
| `GEMINI_API_KEY` | Enables the AI email-copy generator |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile CAPTCHA |
| `CELERY_BROKER_URL` | Redis broker URL |
| `PURGE_RETENTION_DAYS` | Retention window before soft-deleted records are purged (default 90) |
| `SCHEDULE_EDIT_FREEZE_HOURS`, `SCHEDULE_REMINDER_LEAD_HOURS` | Scheduling freeze and reminder window |
| `SITE_URL` | Absolute base URL used in emails and the tracking pixel |

Generate a Fernet key with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Install and migrate
```bash
uv sync
uv run python manage.py migrate
```

### 4. Create an admin user
```bash
uv run python manage.py createsuperuser
```

### 5. Run the development server
```bash
uv run python manage.py runserver
```
Visit `http://127.0.0.1:8000/`.

### 6. Background workers (optional but required for sending, scheduling, and document generation)
With Redis running, start a Celery worker and the Beat scheduler:
```bash
# Worker (use --pool=solo on Windows)
uv run celery -A core worker -l info --pool=solo

# Beat scheduler (scheduled campaigns, reminders, stale-send recovery, weekly purge)
uv run celery -A core beat -l info
```

---

## Maintenance Commands

```bash
# Physically purge soft-deleted records past the retention window
uv run python manage.py purge_soft_deleted

# Report (or delete) media files no longer referenced by any record
uv run python manage.py find_orphan_media          # dry run
uv run python manage.py find_orphan_media --delete

# Encrypt any signature files written before encryption-at-rest was enabled
uv run python manage.py encrypt_signature_files
```

---

## Testing

```bash
uv run python manage.py test
```
Celery tasks run synchronously during tests, so no broker is required. The suite
covers campaign lifecycle and validation, soft-delete and purge rules, file
cleanup, signature processing and access control, encryption at rest, scheduling,
and CSV handling.

---

## Production Notes

- Set strong, unique `SECRET_KEY`, `SMTP_ENCRYPTION_KEY`, and `FILE_ENCRYPTION_KEY`
  values, and run with `DEBUG=False`.
- Do not serve the `media/signatures/` and `media/generated_documents/` subtrees
  as public static files. Those are delivered only through authenticated views;
  see `docs/media-security.md`.
- Run at least one Celery worker and one Beat scheduler so sending, scheduling,
  reminders, stale-send recovery, and the weekly purge all run.
