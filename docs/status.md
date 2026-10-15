# SendGlob (EmailOPS) — Project & Billing Status Report

**Document Date:** September 1, 2026  
**Application Name:** SendGlob (*formerly EmailOPS*)  
**Core Purpose:** High-reliability bulk email delivery, personalized multi-field document generation, campaign scheduling, and Pay-As-You-Grow credit billing platform.  
**Tech Stack:** Python 3.13 | Django 6.0.x | SQLite (Dev) / PostgreSQL (Prod) | Celery 5.x + Redis | Pillow | Fernet (AES-128-CBC) | Bootstrap 5  

---

## 1. Executive Summary

SendGlob has evolved from a simple email campaign tool into a comprehensive, multi-tenant document generation and bulk email platform with an integrated Pay-As-You-Grow (PAYG) credit system, staff operations console, and public marketing surface.

### Current Overall Project Health:
- **Core Workflow Stability:** **Production-Ready.** Email campaigns, document generation, scheduling, and background delivery pipelines are fully functional and covered by unit/integration tests.
- **Billing & Payment Integration:** **Feature-Complete (Code & Architecture).** Full credit ledger, Razorpay gateway integration (with local manual/stub fallback), webhook idempotency, sequential receipts, and feature gating are implemented. Ready for live gateway credentials.
- **Operations & Security:** **Production-Grade.** Dedicated `/ops/` console, Fernet encryption at rest for sensitive media (signatures), authentication-gated media serving, Turnstile CAPTCHA protection, and audit logging are active.
- **Bug Resolution Rate:** **98.2%** (54 out of 55 recorded bugs resolved in `docs/bugs.md`).

---

## 2. Deep-Dive: Billing & Payment Status

The billing system is designed around sustainability and fair pricing: **Document Generation is the billable unit**, while email delivery remains free because emails are dispatched via the user's own SMTP/Gmail account.

### 2.1 Billing Economics & Matrix

| Action / Resource | Credit Cost | Notes |
|---|---|---|
| **Document Generation** | **1 Credit / doc** | Billed on successful rendering; delivery via email is 100% free |
| **AI Template Generation** | **2 Credits / prompt** | Covers metered external LLM API costs (Gemini) |
| **Email Delivery** | **0 Credits** | Sent via user's own connected SMTP account |
| **Signup Bonus** | **+50 Credits** | Automatically granted to every new registered user |
| **Campaign Scheduling** | **0 Extra Credits** | Standard feature; recurring scheduling gated by plan |

---

### 2.2 What Has Been Done in Billing & Payments

#### 1. Immutable Credit Wallet & Ledger (`billing/models.py`, `billing/wallet.py`)
- **`CreditWallet`:** Holds the user's authoritative balance, `lifetime_earned`, and `lifetime_spent`.
- **`CreditTransaction`:** Append-only ledger recording signed credit changes (`+` credits / `-` debits), transaction type, reference ID, and audit snapshots (`balance_after`).
- **Atomic Concurrency Protection:** `wallet.py` uses `select_for_update()` inside `transaction.atomic()` to prevent race conditions or overdrafts across concurrent Celery workers.
- **Graceful Error Handling:** Raises `InsufficientCreditsError` when balances are depleted, cleanly flagging failing tasks without silent corruption.

#### 2. Products, Packs & Plans (`billing/models.py`, `billing/management/commands/setup_billing_plans.py`)
- **`CreditPack` (PAYG Bundles):** One-time credit bundles supporting INR and USD pricing:
  - *INR:* 300 credits (₹149), 1,000 credits (₹399), 3,000 credits (₹999), 8,000 credits (₹2,199), 20,000 credits (₹4,499).
  - *USD:* 300 credits ($3.99), 1,000 credits ($9.99), 3,000 credits ($24.99), 8,000 credits ($54.99), 20,000 credits ($109.99).
  - *Rule:* PAYG credits never expire.
- **`Plan` & `Subscription` (Recurring Plans):**
  - Starter (1,500 credits/mo @ ₹349 / $9), Growth (5,000 credits/mo @ ₹899 / $22), Pro (15,000 credits/mo @ ₹2,199 / $55).
  - Unlocks higher SMTP config limits, recurring scheduling, and priority features.

#### 3. Razorpay Payment Gateway & Checkout (`billing/gateway.py`, `billing/views.py`)
- **Gateway Isolation:** All Razorpay API calls are encapsulated in `billing/gateway.py`.
- **Dual-Mode Operation (Live + Stub):** When Razorpay API keys are configured and the SDK is installed, real Orders and Subscriptions are created. If keys are missing, the system falls back to a stub/manual mode for local testing without breaking app workflows.
- **HMAC Signature Verification:** Verifies client callbacks and incoming webhook signatures with `hmac-sha256`.

#### 4. Idempotent Webhook Processing (`billing/webhooks.py`)
- **Store-Then-Process Architecture:** Webhooks are recorded in `WebhookEvent` with unique event IDs before processing. Replays return `duplicate` without double-crediting.
- **Supported Webhook Events:**
  - `payment.captured`: Credits wallet, records `Payment`, and triggers receipt generation.
  - `payment.failed`: Records failed payment attempts for audit.
  - `subscription.charged`: Activates subscription and grants cycle credits.
  - `subscription.cancelled`: Marks subscription cancelled at period end.
  - `refund.processed`: Flags payment as refunded.

#### 5. Sequential Receipting & PDF Generation (`billing/receipts.py`, `billing/pdfwriter.py`)
- **Monotonic Receipt Numbering:** Per-year locked counter (`ReceiptCounter`) produces gap-free numbers (e.g., `SG-2026-000001`).
- **Frozen Identity Snapshot:** Copies billing address, user name, and tax ID from `BillingProfile` at payment time so future edits do not mutate historical records.
- **Non-Tax Receipt Disclaimer:** Emits compliant non-tax payment receipts until GST registration is activated.
- **Secure Receipt Downloads:** PDF receipts are served exclusively through ownership-verified views (`billing:receipt_download`).

#### 6. Feature & Credit Gating (`billing/gating.py`)
- **Server-Side Authorization:**
  - `@require_credits(amount)` view decorator.
  - `PlanFeatureRequiredMixin` for class-based views.
  - `can_use_recurring_scheduling(user)` and `max_smtp_configs(user)`.
  - `recipient_cap(user)`: Enforces free-tier recipient limits (50 recipients) while leaving paid tiers uncapped.
- **Global Context Processor:** Injects `billing.balance`, `billing.tier`, and `billing.low_balance` into all templates.

#### 7. Worker Integration & Metering
- **Document Generation Tasks (`documents/tasks.py`):** Checks affordability before processing each recipient; debits 1 credit per successful document. Failed renderings are not charged. If out of credits, recipients are marked with error message `Stopped: out of credits.`
- **AI Content Generation (`emails/views.py`):** Verifies balance and debits 2 credits upon successful Gemini prompt generation.

#### 8. Accounting, Reconciliation & Cost Floor (`billing/accounting.py`)
- **Revenue Rollups:** Trailing-month revenue analytics broken down by currency (INR/USD) and product type (Pack/Plan).
- **Cost Floor Analysis:** Compares monthly revenue against `BILLING_INFRA_COST_FLOOR` to tell the operator if infrastructure costs are covered.
- **Automated Reconciliation:** Checks for orphan payments, missing receipts, or unprocessed webhook events.
- **CLI Commands:** `setup_billing_plans`, `grant_credits`, `reconcile_payments`, `revenue_report`.

---

### 2.3 What Has to Be Done in Billing & Payments

| Task | Priority | Status | Description / Next Steps |
|---|---|---|---|
| **1. Production Razorpay Keys** | **High** | Pending Prod Deploy | Add production `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` to production environment `.env`. |
| **2. End-to-End Gateway Sandbox Run** | **Medium** | Ready for Test Run | Run live interactive checkout in browser using Razorpay Test Mode keys (UPI, Cards, NetBanking). |
| **3. Business Registration & GST Activation** | **Medium** | Dormant Path Ready | When formal business registration occurs: Set seller GSTIN in settings, enable tax calculation breakdown (CGST/SGST/IGST), and flip `is_tax_invoice=True` in `Receipt`. (Schema already supports this without migration). |
| **4. Automated Dunning & Retry Schedule** | **Low** | Designed | Extend past-due handling to email users with an automated reminder when recurring subscription renewal charges fail. |
| **5. Periodic Credit Rollover Cap Worker** | **Low** | Optional Policy | If enforcing strict 1-cycle credit rollover caps on subscriptions, add a monthly Celery Beat task to reconcile expiring unused subscription credits. |

---

## 3. Whole Project Functional Status

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                   SENDGLOB PLATFORM                                     │
├─────────────────┬─────────────────┬──────────────────┬─────────────────┬────────────────┤
│  Public Pages   │  Accounts &     │ Email Campaigns  │  Document &     │ Operations     │
│   & Marketing   │ Authentication  │  & Delivery      │  Certificate    │    Console     │
│    (pages/)     │  (accounts/)    │   (emails/)      │  (documents/)   │ (opsconsole/)  │
├─────────────────┼─────────────────┼──────────────────┼─────────────────┼────────────────┤
│ • Landing page  │ • Allauth Email │ • Template Build │ • Multi-field   │ • System Health│
│ • Unified Price │ • Turnstile     │ • Campaign State │ • Custom Fonts  │ • User Admin   │
│ • Trust & Legal │ • Google OAuth  │ • Celery Sender  │ • Signatures    │ • Impersonation│
│ • Contact Form  │ • Fernet SMTP   │ • Open Tracking  │ • Metered Task  │ • Audit Logs   │
│ • Static Docs   │ • User Profile  │ • 24h Scheduling │ • Auth Media    │ • Kill Switch  │
└─────────────────┴─────────────────┴──────────────────┴─────────────────┴────────────────┘
```

---

### 3.1 Accounts & Authentication (`accounts/`)

#### What Has Been Done:
- **Email-Centric Authentication:** Custom user model (`CustomUser`) with email login and decoupled UUID usernames (resolving Allauth duplicate account collisions).
- **Bot & Abuse Protection:** Cloudflare Turnstile CAPTCHA integrated on Signup, Login, and Password Reset forms.
- **Social Login:** Google OAuth2 with PKCE and automatic account linking based on verified email.
- **SMTP Configuration Security:**
  - AES-128-CBC encryption of SMTP passwords using Fernet (`SMTP_ENCRYPTION_KEY`).
  - Active/Inactive toggle per user.
  - Live SMTP test connection tool (`accounts:smtp_test`) with diagnostic test email dispatch.
- **User Profile Fields:** Added `full_name`, `organization`, `phone`, `country`, and `user_timezone` to drive display names, region-based pricing, and timezone-accurate scheduling.

#### What Has to Be Done:
- **Two-Factor Authentication (2FA/TOTP):** Optional authenticator app (TOTP) support for security-conscious users.
- **Self-Service Account Deletion:** "Delete my account" workflow with cascading soft-deletion and GDPR-compliant data export.

---

### 3.2 Email Campaigns & Delivery Engine (`emails/`)

#### What Has Been Done:
- **Email Template Builder:**
  - Multiple responsive layouts: *Simple*, *Announcement*, *Newsletter*, and *Custom HTML/Text*.
  - Live preview rendering using Django's template engine inside an iframe.
  - Brand color picking and customizable logo sizing (height 20–150px).
  - AI copy generation powered by Gemini API (metered at 2 credits).
- **Campaign State Machine:**
  - States: `draft` → `scheduled` → `queued` → `sending` → `completed` / `failed` / `cancelled`.
  - Immutable locking during active states (`queued`, `sending`, `scheduled`) preventing destructive edits.
  - Graceful action validations (preventing zero-recipient sends, double-queuing, or invalid resumes).
- **Recipient Ingestion:**
  - Quick text entry (comma/newline separated) and CSV file uploads.
  - Preserves duplicate emails with distinct contexts.
- **High-Throughput Delivery Pipeline (`emails/tasks.py`):**
  - Concurrency locks with `select_for_update(skip_locked=True)` on recipient batches.
  - Exponential backoff retry mechanism for transient SMTP connection errors (`SMTP_CONNECT_MAX_ATTEMPTS = 4`).
  - Stale sending recipient reaper task to recover stuck workers.
  - Open tracking pixel injection with dynamic reverse URL resolution (`/dashboard/track/<uuid>`).
- **Scheduling & Recurrence Engine:**
  - Automated Celery Beat task (`trigger_scheduled_campaigns`) checking due campaigns.
  - 24-hour edit freeze window and automated pre-run email reminders (`send_schedule_reminders`).
  - Recurrence intervals (*Daily*, *Weekly*, *Monthly*, *Quarterly*, *Yearly*) with automatic campaign cloning for subsequent runs.

#### What Has to Be Done:
- **Click Tracking Engine:** URL wrapping and redirection tracking for links inside email bodies.
- **Unsubscribe & Suppression Lists:** One-click unsubscribe link injection and domain/email suppression lists to prevent re-sending to opted-out contacts.
- **Bounce & Complaint Processing:** Inbound mailbox monitoring or webhook parser for hard/soft bounce classification.

---

### 3.3 Personalized Document & Certificate Generation (`documents/`)

#### What Has Been Done:
- **Multi-Field Document Template (`DocumentTemplate`):**
  - Supports image (PNG/JPG) backgrounds and PDF outputs.
  - Global typography mode or per-field typography (font size, custom TTF font files, colors, left/center/right alignment).
- **Dynamic CSV Mapping (`DocumentTextField`):**
  - Arbitrary number of text fields mapped directly from CSV column headers.
  - Coordinate bounding boxes (`x_start`, `x_end`, `y_coordinate`).
- **Encrypted Digital Signatures (`DocumentSignature`, `DocumentSignatureField`):**
  - Users can upload transparent signature images.
  - Stored encrypted at rest via `EncryptedFileSystemStorage` using Fernet.
  - Placed dynamically on generated certificates/documents.
- **Celery Batch Generation Task (`documents/tasks.py`):**
  - High-speed Pillow/PDF compositing.
  - Integrated 1-credit per document deduction.
  - Per-recipient error logging; auto-fail trigger if all documents fail before dispatch.
- **Media Security Layer:**
  - Sensitive media (templates, signatures, generated documents) are served via authenticated, ownership-checked views rather than public direct URLs.

#### What Has to Be Done:
- **Visual Drag-and-Drop Canvas Designer:** Interactive browser canvas for placing, resizing, and positioning text/signature fields over background images without manually typing pixel coordinates.
- **Dynamic QR Code Field:** Built-in QR code generator field mapping dynamic recipient verification URLs onto documents.
- **Multi-Page Document Generation:** Support for generating multi-page PDFs from multi-page document templates.

---

### 3.4 Operations Console (`opsconsole/`)

#### What Has Been Done:
- **Staff-Only Portal (`/ops/`):** Strict `is_staff` / `is_superuser` authorization and isolation from user-facing routes.
- **System Health & Observability:**
  - Live Redis connectivity check and Celery active task queue inspection.
  - Database table sizes and storage disk utilization gauges.
  - Real-time application error log tail viewer.
- **Operational Controls & Flags (`opsconsole/flags.py`):**
  - Global Emergency Kill Switch (`campaign_sending_disabled`).
  - Maintenance Mode toggle (`maintenance_mode`).
  - Selective feature disabling (`ai_generation_disabled`, `registration_disabled`).
- **User Administration & Auditing:**
  - Searchable user lists with active plan and wallet balances.
  - Operator credit adjustments with mandatory reason logging.
  - Read-only User Impersonation ("View As User") with persistent warning banner and full start/stop audit tracking (`OperatorAuditLog`).
- **Financial Analytics:** Live dashboard tracking monthly captured revenue, paying user counts, and net performance against `BILLING_INFRA_COST_FLOOR`.

#### What Has to Be Done:
- **Automated Webhook Alerts:** Connect threshold alerts (e.g. disk > 85%, Redis down, error spikes) to Slack/Discord webhooks or SMS.
- **One-Click Backup Trigger:** Operator console button to trigger database snapshots and media archives.

---

### 3.5 Public Marketing Pages (`pages/`)

#### What Has Been Done:
- **Marketing Pages:** Modern, theme-aware Landing page, Features breakdown, Security & Trust architecture page.
- **Unified Pricing (`pages/views.py`, `templates/partials/pricing_table.html`):**
  - Single source of truth reading directly from active `CreditPack` and `Plan` database models.
  - Region-aware pricing (INR for Indian users, USD for global users).
- **Review-Ready Legal Pages:** Terms of Service, Privacy Policy, Refund & Cancellation Policy, Acceptable Use / Anti-Spam Policy.
- **Contact Form:** Database-backed contact submission with Turnstile protection, surfacing inquiries in the Operations Console.

#### What Has to Be Done:
- **Knowledge Base & Documentation:** User guides with screenshots demonstrating CSV formatting, SMTP password creation, and document designing.
- **SEO & Social Metadata:** OpenGraph tags, Twitter cards, and Schema.org structured data.

---

### 3.6 Core Infrastructure & Storage (`core/`)

#### What Has Been Done:
- **Data Lifecycle & Soft Deletion (`SoftDeleteModel`):**
  - Project-wide soft deletion preserving foreign-key integrity for historical reporting.
  - Weekly Celery Beat purge task (`purge_soft_deleted_records`) physically removing soft-deleted rows older than 90 days.
  - Strict guardrails permanently protecting financial/payment records from purging.
- **Media File Security & Storage:**
  - Custom `EncryptedFileSystemStorage` encrypting sensitive binary files (signatures) on disk.
  - Physical file cleanup signals removing orphaned files on hard-deletion.
- **Branding & Context Processors:** Global `APP_NAME` and `APP_TAGLINE` settings propagated across all views and emails.

#### What Has to Be Done:
- **Cloud Object Storage Adapter:** Support for Amazon S3 / Cloudflare R2 storage backends when deploying to multi-server containerized architectures.
- **Production Containerization:** Official `Dockerfile` and `docker-compose.yml` configurations with Gunicorn, Celery workers, Celery Beat, Redis, and PostgreSQL.

---

## 4. Bugs and Technical Debt Summary

- **Total Tracked Bugs:** 55 issues logged in `docs/bugs.md`.
- **Closed / Resolved:** 54 issues.
- **Open Issues:** 1 minor test-isolation issue:
  - `[B-055]` *Operational-flag cache leaks across tests*: `MaintenanceModeMiddleware` test leaves a cached flag in memory during full test runs if not cleared in `tearDown`.
- **Legacy Components:**
  - `certificates/` app is deprecated in favor of `documents/` and is retained solely for legacy database migration history.

---

## 5. Production Launch Checklist & Roadmap

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                           ROADMAP TO PRODUCTION LAUNCH                            │
└───────────────────────────────────────────────────────────────────────────────────┘
   PHASE 1: PRE-FLIGHT CONFIGURATION (Current Stage)
   ├── [ ] Configure Production .env (SECRET_KEY, SITE_URL, SMTP_ENCRYPTION_KEY)
   ├── [ ] Add Live Razorpay Credentials (KEY_ID, KEY_SECRET, WEBHOOK_SECRET)
   ├── [ ] Add Production Google OAuth Credentials & Cloudflare Turnstile Keys
   └── [ ] Run `python manage.py setup_billing_plans` on production database

   PHASE 2: DEPLOYMENT & INFRASTRUCTURE
   ├── [ ] Set up PostgreSQL and Redis instances
   ├── [ ] Configure Gunicorn / Uvicorn systemd services behind Nginx reverse proxy
   ├── [ ] Configure Celery Worker and Celery Beat systemd daemons
   └── [ ] Set up SSL certificates (Let's Encrypt / Cloudflare)

   PHASE 3: POST-LAUNCH ENHANCEMENTS
   ├── [ ] Interactive Visual Drag-and-Drop Canvas Designer for Document Templates
   ├── [ ] Click Tracking & One-Click Unsubscribe Engine
   ├── [ ] Discord/Slack Webhook Alerting for Operations Console
   └── [ ] Business Registration & GST Tax Invoicing Activation
```

---

## 6. Conclusion

SendGlob is in a **robust, advanced, and production-ready engineering state**. The foundational architecture, campaign workflows, document rendering pipelines, billing ledger, and security models are fully built, tested, and aligned with project design guidelines. 

Launching to production primarily requires configuring production environment secrets, attaching live Razorpay credentials, and provisioning server daemons.
