# EmailOPS Bug Tracker

Tracks bugs, inconsistencies, and technical debt discovered while working in the codebase.

Entries follow the format defined in `CLAUDE.md`: discovery date, affected area,
severity, status, and a brief description with a fix sketch. Resolved entries
are kept (not deleted) and marked **Completed** with a resolution date.

---

## Open

### [TD-B03] Receipts are not GST tax invoices
- **Discovered**: 2026-10-15
- **Area**: `billing/receipts.py`
- **Severity**: Not applicable at present
- **Status**: **Deferred** (2026-10-15)

Receipts carry `tax_rate`/`tax_amount`/`is_tax_invoice` fields but always issue as plain payment receipts. The operator is not a registered company and is not GST-registered, so plain receipts are correct today. Revisit on registration: tax computation (CGST/SGST vs IGST by place of supply), seller GSTIN, and tax-invoice format.

---

## Closed / Resolved

### [B-060] `test_standard_campaign_persists_schedule` fails since recurring scheduling became plan-gated
- **Discovered**: 2026-10-15
- **Area**: `documents/tests.py` (`SchedulingPersistenceTests`), related to B-057
- **Resolved**: 2026-10-15
- **Severity**: Low - test-only; the suite is red
- **Status**: **Closed**

B-057 made recurring schedules require a plan with `recurring_scheduling`. This test posts `is_recurring=on` as a free-tier user, so the form rejects it and `EmailCampaign.objects.get(name='StdSched')` raises `DoesNotExist`.

**Resolution**: `SchedulingPersistenceTests.setUp` now gives the user an active subscription to a plan with `recurring_scheduling=True`. Added `test_free_tier_cannot_create_recurring_campaign`, which expires that subscription and asserts the recurring campaign is refused.

---

### [TD-B01] Plan switching (upgrade/downgrade) was not supported
- **Discovered**: 2026-10-15
- **Resolved**: 2026-10-15
- **Area**: `billing/views.py`, `billing/fulfillment.py`, `billing/webhooks.py`, `billing/gateway.py`
- **Severity**: Low - product gap
- **Status**: **Closed**

A subscriber could only change plans by cancelling and waiting for the period to end.

**Resolution**: Users can switch plans from Manage plan; the switch takes effect at the next renewal (Razorpay `subscription.edit` with `schedule_change_at=cycle_end`), so every charge is a full plan price and every grant a full cycle of credits, with no proration. `Subscription.scheduled_plan` / `scheduled_gateway_plan_id` record the pending switch; `subscription.updated` and `subscription.charged` apply it when the gateway reports the new gateway plan. Users can withdraw a pending switch, and cancelling clears it. Manual plans switch through `activate_plan` at renewal.

---

### [TD-B02] Local Plan price was not synced to the Razorpay plan
- **Discovered**: 2026-10-15
- **Resolved**: 2026-10-15
- **Area**: `billing/admin.py`, `billing/models.py`, `billing/checks.py`
- **Severity**: Low - display/charge mismatch on operator error
- **Status**: **Closed**

Editing `Plan.price` changed only the displayed price; subscribers were charged the immutable Razorpay plan's amount.

**Resolution**: In live mode the Plan admin form creates a new Razorpay plan whenever price, currency or cycle changes (or none is linked) and stores its id; an operator-entered id is respected. Existing subscribers are grandfathered on the gateway plan they bought, tracked in `Subscription.gateway_plan_id`. New command `sync_razorpay_plans` links plans created before keys were configured, and deploy check `billing.W003` flags active plans without a gateway plan.

---

### [B-061] Production settings allowed insecure defaults
- **Discovered**: 2026-10-15
- **Resolved**: 2026-10-15
- **Area**: `core/settings.py`
- **Severity**: **High** - session forgery and Host-header poisoning possible if deployed with defaults
- **Status**: **Closed**

With `DEBUG=False` the app still started with the committed `django-insecure` `SECRET_KEY` and `ALLOWED_HOSTS=*`, cookies were not marked secure, there was no HTTPS redirect or HSTS (`check --deploy` warnings W004/W008/W012/W016), and no `LOGGING` configuration, so gateway and webhook errors had no defined destination.

**Resolution**: Startup now raises `ImproperlyConfigured` for the insecure key or a wildcard host when `DEBUG=False`. Production enables secure session/CSRF cookies, `SECURE_SSL_REDIRECT`, HSTS (30 days by default, configurable), `nosniff`, and an opt-in `SECURE_PROXY_SSL_HEADER` for TLS-terminating proxies. Added console `LOGGING`. Remaining deploy warnings (HSTS subdomains/preload) are deliberate opt-ins documented in `docs/configuration.md`.

---

### [B-059] Billing purchase flow not production ready: no order snapshot, broken live-mode fallbacks, lost late captures, unsafe refunds and cancellations
- **Discovered**: 2026-10-15
- **Resolved**: 2026-10-15
- **Area**: `billing/` (models, gateway, webhooks, views, receipts, accounting), `templates/billing/`, `core/celery.py`, `core/settings.py`
- **Severity**: **High** - financial accuracy and customer-trust risk
- **Status**: **Closed**

Issues found while reviewing the billing module end to end:

1. **No order record.** Pack credits were derived at capture time from gateway notes and the *current* catalogue, so a repriced pack changed what an in-flight purchase granted, and manual-mode references (`manual_order_pack-<id>`) were shared by every buyer of the same pack.
2. **Live-mode failures faked an order.** When Razorpay was configured but an API call failed, `create_order` / `create_subscription` returned a `manual_` stub and the page still rendered the live checkout widget with that invalid id. A plan without `razorpay_plan_id` did the same.
3. **Checkout callback never verified.** `verify_payment_signature` existed but was unused; users waited for webhook delivery with a generic "payment is being confirmed" message, and a lost webhook meant nothing was ever granted.
4. **Late capture after failure was dropped.** `payment.failed` stored a FAILED `Payment`; a later `payment.captured` for the same id saw the row and returned without crediting.
5. **Refunds.** Every refund (including partial) marked the payment fully refunded and clawed back all credits; a second partial refund was ignored; a refund where the user had no balance left produced a permanent false reconciliation alert.
6. **Cancellation could lie.** If the gateway cancel failed, the view still set `cancel_at_period_end` and showed success, so the user believed they were cancelled while still being billed.
7. **Plans never ended without webhooks.** No `subscription.completed` handler, and nothing expired an ACTIVE plan whose period passed (lost webhook, or any manual plan). Stale PENDING subscriptions accumulated.
8. **Multiple concurrent plans.** Buying a second plan while subscribed created a second active subscription with an ambiguous tier.
9. **Concurrent duplicate webhooks** could both pass the `processed_at` check.
10. **Manual payments had no proper path.** `grant_credits` added credits with no Payment or Receipt, so revenue reports and receipts missed manual sales.
11. **Tests could hit the real gateway** when `.env` held Razorpay keys.
12. Smaller items: float money conversion from paise, silent PDF render failures, profile form without validation, unpaginated history without payment statuses, catalogue rows deletable in admin (breaking history), N+1 queries in `reconcile()`.

**Resolution**:
- Added `PackOrder` (price/credits snapshot, unique reference, gateway order id) and `Refund` (per gateway refund id, credits owed vs reversed). `Payment` and `Receipt` snapshot an item `description`; `Payment.pack/plan` are now `PROTECT`; added `PARTIALLY_REFUNDED`.
- New `billing/fulfillment.py` is the single money-to-credits path used by webhooks, the checkout callback and manual commands; every function is atomic, row-locked and idempotent, and promotes a FAILED payment on late capture.
- `gateway.py` raises `GatewayError` in live mode instead of faking orders; manual stubs get unique ids; added `verify_subscription_signature`; subscription `total_count` depends on billing cycle.
- New `checkout_complete` view verifies the Razorpay signature and fulfils immediately (webhook remains the fallback). Checkout JS posts the signed ids and shows payment failures inline.
- Webhooks: processing runs under a lock on the event row; added `subscription.completed`; refunds are proportional and idempotent per refund id; amounts use `Decimal`.
- `cancel_subscription` only changes local state after the gateway confirms; past-due plans cancel immediately.
- `checkout_plan` blocks a second concurrent plan.
- New hourly Celery task `billing.tasks.expire_lapsed_subscriptions_task` (grace window and checkout TTL are settings).
- New commands `fulfill_pack_order` and `activate_plan` for manual payments, producing real Payments and Receipts.
- Deployment checks (`billing.E001-E003`, `W001-W002`) for gateway misconfiguration.
- Receipts include seller details from settings and log render failures; reconciliation uses set-based queries and refund records; revenue is net of refunds.
- Test settings blank the Razorpay keys. Added regression tests for each item.

---

### [B-056] Razorpay subscription webhooks double-credit on charge, miss Payment and Receipt creation, leave renewal period dates blank, and cancel_subscription does not invoke gateway
- **Discovered**: 2026-10-13
- **Resolved**: 2026-10-13
- **Area**: `billing/webhooks.py`, `billing/gateway.py`, `billing/views.py`
- **Severity**: **High** - financial accuracy risk; double credit grants on subscription charges, missing legal/accounting payment records, un-synchronized renewal dates, and subscriptions not cancelled upstream with payment provider
- **Status**: **Closed**

When a subscription renewed, Razorpay emitted both `subscription.charged` and `payment.captured`. Both handlers independently credited the user wallet, granting double credits. Furthermore, if `subscription.charged` executed without an initial payment payload, it failed to record a `Payment` object or issue a `Receipt`. The handler also never populated `current_period_start` and `current_period_end` timestamps on `Subscription`, leaving dashboard renewal dates blank (`-`). Finally, `cancel_subscription` only updated local database flags without notifying Razorpay (`client.subscription.cancel`), leaving customer recurring charges active on the gateway.

**Resolution**: Updated `webhooks._subscription_charged` to extract the payment entity, create a `Payment` row and generate a `Receipt` idempotently if not already present, sync `current_period_start` and `current_period_end` from gateway timestamps, and deduplicate credit ledger grants via transaction checks on `reference_id` across both handlers. Added `gateway.cancel_subscription` with API error handling and connected it to `cancel_subscription`. Also implemented handlers for `subscription.halted` (`PAST_DUE`), `subscription.paused` (`PAST_DUE`), and `subscription.resumed` (`ACTIVE`).

---

### [B-057] Billing feature gating and free tier limits unenforced across application
- **Discovered**: 2026-10-13
- **Resolved**: 2026-10-13
- **Area**: `accounts/views.py`, `emails/forms.py`, `emails/views.py`, `billing/gating.py`
- **Severity**: **Medium** - business invariant violation; free users could add unlimited SMTP accounts, configure recurring schedules without paid plans, and bypass recipient limits on campaigns
- **Status**: **Closed**

The feature gating utilities in `billing/gating.py` (`can_use_recurring_scheduling`, `max_smtp_configs`, `recipient_cap`) were modeled and tested in isolation but never invoked in application forms and views. Users on free tiers could create unlimited SMTP configurations, configure recurring campaigns, and queue campaigns to arbitrary recipient counts without plan enforcement.

**Resolution**: (1) Enforced `gating.max_smtp_configs(request.user)` in `accounts/views.py` (`smtp_config_create` & `smtp_config_list`) and updated the UI button to indicate plan limits with an upgrade nudge. (2) Enforced `gating.can_use_recurring_scheduling` in `emails/forms.py` (`_ScheduleFieldsMixin.clean`) so recurring schedules require an active plan with the feature enabled. (3) Enforced `gating.recipient_cap` in `emails/views.py` (`campaign_create`, `campaign_edit`, `campaign_queue`, `campaign_restart`) so free tier campaigns cannot exceed the recipient cap.

---

### [B-058] Payment reconciliation and ledger integrity checks incomplete; unhandled CLI exceptions on negative credit overdraft
- **Discovered**: 2026-10-13
- **Resolved**: 2026-10-13
- **Area**: `billing/accounting.py`, `billing/management/commands/grant_credits.py`, `billing/management/commands/reconcile_payments.py`
- **Severity**: **Low** - accounting gap & CLI crash
- **Status**: **Closed**

`accounting.reconcile()` only checked for captured payments without receipts and unprocessed webhooks. It did not verify whether refunded payments had ledger clawbacks, whether captured webhook events matched local payment records, or whether wallet balances matched the sum of append-only ledger transactions. In addition, the `grant_credits` management command crashed with an unhandled exception when an administrator attempted a negative deduction larger than the wallet balance.

**Resolution**: Expanded `accounting.reconcile()` to verify refund reversals, unmatched webhook payments, and wallet-to-ledger mathematical consistency. Added `--fix-missing-receipts` self-healing option to `reconcile_payments`. Wrapped `grant_credits` to handle `InsufficientCreditsError` and `ValueError` as clean CLI `CommandError` messages.

---

### [B-055] Operational-flag cache leaks across tests (maintenance mode 503s unrelated tests)
- **Discovered**: 2026-07-04
- **Resolved**: 2026-09-01
- **Area**: `opsconsole/flags.py` / `opsconsole/tests.py` (`MaintenanceMiddlewareTests`)
- **Severity**: **Low** - test-isolation only; production behaviour is correct
- **Status**: **Closed**

`opsconsole.flags` caches flag reads in the default cache (LocMem, 5s TTL).
`TestCase` rolls back the DB between tests but did not clear the cache, so after
`MaintenanceMiddlewareTests` enabled `maintenance_mode`, subsequent tests running within the TTL could encounter leaked flag state.

**Resolution**: Added `invalidate_all()` to `opsconsole/flags.py` and connected automated cache invalidation in `_Mixin.tearDown()` in `opsconsole/tests.py` so all flag keys (`global_send_paused`, `maintenance_mode`, `send_throttle`) are reliably invalidated between test runs. Full suite green.

### [B-050] `campaign_status_api` resolves campaigns via unfiltered `objects` manager
- **Discovered**: 2026-07-04 (bug hunting day)
- **Area**: `emails/views.py` (line 213)
- **Severity**: **Medium** - soft-deleted campaign reachable by URL; leaks recipient-level status/email data
- **Resolved**: 2026-07-04
- **Status**: **Closed**

`campaign_status_api` (the AJAX polling endpoint backing the Recipients Log
table) does `get_object_or_404(EmailCampaign, pk=pk, user=value_request.user)`
instead of `EmailCampaign.active`. Every other campaign view was fixed for this
exact pattern under B-031, and the regression guardrail
`test_soft_deleted_campaign_actions_return_404` exists, but its coverage list
(`campaign_detail`, `campaign_queue`, `campaign_cancel`, `campaign_restart`)
never included this endpoint, so the gap slipped through. A user with a stale
link to a soft-deleted campaign's status endpoint can keep polling its live
recipient rows (status, error messages, timestamps) after "deletion."

**Resolution**: Switched the lookup to `get_object_or_404(EmailCampaign.active, ...)`
and extended `test_soft_deleted_campaign_actions_return_404` with a GET-endpoint
loop covering `campaign_status_api`, so any future regression to the unfiltered
manager on this endpoint fails the guardrail. Full suite green.

---

### [B-051] Editing a scheduled campaign down to zero recipients can leave it armed with a schedule
- **Discovered**: 2026-07-04 (bug hunting day)
- **Area**: `emails/views.py` (lines 768-774), `emails/forms.py` (`apply_schedule`, lines 128-136)
- **Severity**: **Medium** - violates the zero-recipient invariant for scheduled sends; produces a spurious "completed" campaign with 0 emails sent
- **Resolved**: 2026-07-04
- **Status**: **Closed**

`campaign_edit`'s schedule-reapply branch only has two cases:
```python
if next_run_at and campaign.recipients.exists():
    campaign.status = 'scheduled'
elif campaign.status == 'scheduled' and not next_run_at:
    campaign.status = 'draft'
campaign.save()
```
`apply_schedule()` unconditionally computes and sets `campaign.next_run_at`
before either branch runs. If a `'scheduled'` campaign with recipients is
edited with `recipient_handling='replace'` and a CSV that parses to zero valid
rows, existing recipients are deleted and none are re-added. The schedule
fields are resubmitted unchanged, so `next_run_at` comes back truthy. Neither
branch matches (`recipients.exists()` is now False, and `next_run_at` is not
falsy), so `campaign.status` is left at `'scheduled'` with 0 recipients and a
live `next_run_at`. `trigger_scheduled_campaigns` (`emails/tasks.py`) has no
recipient-count check of its own and will promote it to `'queued'`;
`send_campaign_emails_task` self-resolves harmlessly (0 claimable recipients
finalizes as `'completed'`), so nothing crashes, but the campaign silently
becomes a "completed" record that never sent anything, and no zero-recipient
guard ever surfaced a warning to the user, unlike `campaign_create` /
`campaign_queue`.

**Resolution**: Two layers. (1) `campaign_edit` gained an explicit
"scheduled but zero recipients" branch that forces `status='draft'`, clears
`next_run_at`, and warns the user. (2) Defense in depth:
`trigger_scheduled_campaigns` now demotes any due campaign with zero
recipients to `draft` (compare-and-set, `next_run_at=None`) and logs a
warning instead of promoting it to `queued`. Regression test
`test_trigger_demotes_zero_recipient_scheduled_to_draft` added to
`emails/tests_scheduling.py` (`RecurrenceLifecycleTests`). Full suite green.

---

### [B-052] `DynamicCSRFMiddleware` mutates global `CSRF_TRUSTED_ORIGINS` at runtime from client-supplied headers
- **Discovered**: 2026-07-04 (bug hunting day)
- **Area**: `core/middleware.py`
- **Severity**: **Medium** (DEBUG-only, so not a direct prod risk today, but see note below)
- **Resolved**: 2026-07-04
- **Status**: **Closed**

Only runs when `settings.DEBUG` is True, but when active it takes the
request's `Origin` (or `Referer`) header, and if not already trusted,
permanently appends it to the process-wide `settings.CSRF_TRUSTED_ORIGINS`
list for the remaining life of the worker - a global mutation driven entirely
by client input, not scoped to the current request/session. In a shared
debug/staging environment, one request can poison CSRF trust for every
subsequent request handled by that worker process. If this middleware or
similar were ever enabled outside `DEBUG`, it would be a CSRF bypass.

**Resolution**: Deleted `core/middleware.py` and its `MIDDLEWARE` entry
entirely. `CSRF_TRUSTED_ORIGINS` is now static, deploy-time configuration in
`core/settings.py`: built from `SITE_URL`, an optional comma-separated
`CSRF_TRUSTED_ORIGINS` env var (for dev tunnels/LAN IPs), and localhost
defaults under `DEBUG`. No runtime mutation of settings remains. The stale
`middleware.py` reference in `docs/project-structure.md` was removed. Full
suite green.

---

### [B-053] `SignatureAdmin` renders encrypted-at-rest image fields with the default admin widget
- **Discovered**: 2026-07-04 (bug hunting day)
- **Area**: `documents/admin.py` (`SignatureAdmin.readonly_fields`, `original_image`/`processed_image`)
- **Severity**: **Low**
- **Resolved**: 2026-07-04
- **Status**: **Closed**

Per B-047/B-048, `Signature.original_image`/`processed_image` are stored via
`EncryptedFileSystemStorage` and must never be served directly - the module
docstring in `core/storage.py` says so explicitly. Django admin's default
read-only rendering for an `ImageField` builds a thumbnail/link from
`field.url`, which resolves straight to the public `/media/` path. Today this
just produces a broken image in the staff admin (ciphertext isn't a valid
image), but it's an architectural inconsistency with the "never serve
directly" invariant, and would become a real exposure if media serving is
ever misconfigured.

**Resolution**: `SignatureAdmin` now excludes the raw image fields and renders
read-only `original_image_link`/`processed_image_link` methods that link to the
authenticated `documents:signature_image` view instead of `field.url`. Admin
add is disabled (`has_add_permission` returns False) since signatures must be
created through the upload/processing pipeline; creating one in admin would
bypass background removal and encryption. Note: the serving view is scoped to
the owner, so staff viewing another user's record get a 404 from the link
rather than the bytes, which is consistent with the "never serve directly"
invariant.

---

### [B-054] Document generation re-queries template fields per recipient (N+1)
- **Discovered**: 2026-07-04 (bug hunting day)
- **Area**: `documents/tasks.py` (`generate_documents_task`), `documents/utils.py` (`resolve_values`, `resolve_signatures`, `generate_document_image`), `documents/views.py` (`document_edit`, recipient reset loop)
- **Severity**: **Low** (scalability/technical debt, not a correctness bug)
- **Resolved**: 2026-07-04
- **Status**: **Closed**

For each recipient, `save_document_for_recipient` re-queries the same fixed
`template.text_fields.all()` and `template.signature_fields.all()` multiple
times (once in `resolve_values`/`resolve_signatures`, again inside
`generate_document_image`), plus a `Signature.active.filter(...).first()` per
signature field. None of this is cached or prefetched across the recipient
loop. Separately, `document_edit`'s document-template-change handler does one
`.save()` per recipient (needed so the centralized file-cleanup `pre_save`
signal fires), which also costs an extra `SELECT` per row. Fine at small
scale; will not scale to campaigns with thousands of recipients, contradicting
the "Scalability Mindset" standard in CLAUDE.md.

**Resolution**: `generate_documents_task` now fetches
`list(template.text_fields.all().order_by('id'))` and
`list(template.signature_fields.all())` once per run plus a shared
`signature_cache` dict, threading them through
`save_document_for_recipient` -> `resolve_values` / `resolve_signatures` /
`generate_document_bytes` / `generate_document_image` (all gained optional
prefetch parameters that default to the old per-call queries, so single-shot
callers like `document_preview` are unchanged). The signature cache dedupes
`name -> Signature` lookups across recipients. Both recipient reset loops
(`document_edit` in `documents/views.py` and the template-change branch of
`campaign_edit` in `emails/views.py`) were batched into a single
`.update(document_file=None, status='pending')` with explicit physical file
deletion beforehand (bulk update bypasses the `pre_save` cleanup signal;
mirrors the `campaign_restart` pattern from B-043). Full suite green.

---

### [B-040] Sidebar marks "Email Templates" active when viewing Documents
- **Discovered**: 2026-06-22
- **Area**: `templates/base.html` (sidebar navigation)
- **Severity**: **Low** - cosmetic; misleading active state
- **Resolved**: 2026-06-22
- **Status**: **Closed**

The sidebar active state used fragile path-substring tests. The documents pages live
under `.../documents/templates/`, so `'template' in request.path` matched both the
Email Templates link and the Documents link, lighting both at once. The same brittle
pattern affected the other links.

**Resolution**: Switched every sidebar link to match on the resolved URL instead of
the raw path: `request.resolver_match.namespace` plus a check on
`request.resolver_match.url_name` (e.g. Email Templates = namespace `emails` and
`template` in the url_name; Documents = namespace `documents` and not a signature
view). Verified each page now activates exactly one sidebar item.

---

### [B-038] Scheduling is UTC-only (no per-user timezone)
- **Discovered**: 2026-06-22 (scheduling refactor)
- **Area**: `emails/scheduling.py`, `EmailCampaign.schedule_time`, `accounts/models.py`
- **Severity**: **Low** - functional, but times are interpreted in `settings.TIME_ZONE` (UTC) for every user
- **Resolved**: 2026-06-23
- **Status**: **Closed**

The three-mode scheduler interpreted `schedule_time` in `settings.TIME_ZONE` (UTC)
for every user, and the UI hardcoded "UTC" labels.

**Resolution**: Added `CustomUser.timezone` (IANA name, default `UTC`, migration
`accounts/0004`). `emails.scheduling` now resolves the owner's zone via
`resolve_timezone(campaign)` and interprets the wall-clock schedule in that zone:
`compute_next_run` does its day-of-week / day-of-month / time arithmetic in local
time and `_combine(date, time, tz)` localizes before returning the aware UTC value.
`describe_schedule` and the new `timezone_label` show the owner's zone instead of a
fixed "UTC". The campaign form localizes the time-of-day label and computes the
probe in the user's zone; `campaign_detail` localizes `next_run_at` via the `tz`
filter and the `EmailCampaign.schedule_timezone` property. A new account Settings
page (`accounts:settings`, `UserSettingsForm`) lets users pick their timezone.
Owner-less probes and invalid stored names fall back to `settings.TIME_ZONE`/UTC.
DST edge handling remains a deferred follow-up. Tests added in
`emails/tests_scheduling.py` (`TimezoneSchedulingTests`) and `accounts/tests.py`
(`SettingsViewTestCase`); full suite green.

---

### [B-039] Recurrence does not carry attachments to the next cycle
- **Discovered**: 2026-06-22 (scheduling refactor)
- **Area**: `emails/tasks.py` (`arm_next_cycle`)
- **Severity**: **Low** - recurring cycles carry recipients/template/content but not attachments
- **Resolved**: 2026-06-23
- **Status**: **Closed**

`arm_next_cycle` previously skipped copying `CampaignAttachment` rows because the
`post_delete` file-cleanup signal deletes the physical file, so sharing a file across
cloned campaigns would let one deletion remove a file the other still references.

**Resolution**: Added `_copy_attachments(source, clone)`, called from
`arm_next_cycle`. For each source attachment it reads the bytes and writes a fresh
file via `FileField.save(..., ContentFile(content))`, so storage assigns a distinct
path and each cycle owns its own file. Deleting one cycle's attachment no longer
removes another's. A read failure on a single attachment is logged and skipped rather
than aborting the recurrence. Regression test
`test_arm_next_cycle_copies_attachments_independently` in
`emails/tests_scheduling.py` verifies the copy has a distinct path and surviving the
other's deletion; full suite green.

---

### [B-029] `document_campaign_create` view missing `@login_required` decorator
- **Discovered**: 2026-06-20
- **Area**: `documents/views.py` (line 167)
- **Severity**: **High** - security gap; any unauthenticated user can POST to the endpoint
- **Resolved**: 2026-06-20
- **Status**: **Closed**

`document_campaign_create` is the only campaign-creation view without `@login_required`. Every other view in `documents/views.py` has it. Without the decorator, an anonymous user who knows the URL can POST the form; `value_request.user` resolves to `AnonymousUser`, the prerequisite checks silently return nothing, and attempting `EmailCampaign.objects.create(user=user, ...)` with an AnonymousUser throws a database error rather than redirecting to login.

**Fix sketch**: Add `@login_required` directly above `def document_campaign_create(value_request):` (line 167), matching every other view in the file.

---

### [B-030] `certificate_edit` and `certificate_delete` miss `'scheduled'` in LOCKED_STATUSES check
- **Discovered**: 2026-06-20
- **Area**: `certificates/views.py` (lines 43, 84)
- **Severity**: **Medium** - allows editing/deleting a certificate template while a campaign is in `'scheduled'` state, violating the Campaign Lifecycle invariant
- **Resolved**: 2026-06-20
- **Status**: **Closed**

Both views hard-code `status__in=['queued', 'sending']` instead of using `EmailCampaign.LOCKED_STATUSES`. `'scheduled'` is intentionally in `LOCKED_STATUSES` because Celery Beat can promote a scheduled campaign to `queued` at any moment. Editing the certificate template between the schedule check and the promotion would corrupt the in-flight campaign.

`accounts/views.py` (SMTP protection) and `documents/views.py` (document template protection) both use the correct set. Only `certificates/views.py` diverges.

**Fix sketch**:
```python
# certificates/views.py lines 43 and 84 - replace:
status__in=['queued', 'sending']
# with:
status__in=EmailCampaign.LOCKED_STATUSES
```
Also add `from emails.models import EmailCampaign` if not already imported (it is, via `emails.models`).

---

### [B-031] `campaign_queue`, `campaign_cancel`, and `campaign_restart` fetch campaigns via unfiltered `objects` manager
- **Discovered**: 2026-06-20
- **Area**: `emails/views.py` (lines 233, 279, 309)
- **Severity**: **Medium** - soft-deleted campaigns can be queued, cancelled, or restarted
- **Resolved**: 2026-06-20
- **Status**: **Closed**

All three views use `get_object_or_404(EmailCampaign, pk=pk, user=value_request.user)` (unfiltered `objects`) instead of `EmailCampaign.active`. A user who retains a direct URL to a soft-deleted campaign can queue it; `_begin_campaign_send` does guard against `deleted_at is not None` and aborts the send, but not before the campaign's `status` is flipped to `'queued'`. Cancelling or restarting a soft-deleted campaign would also silently mutate its state.

All other campaign views (`campaign_detail`, `campaign_edit`, `campaign_delete`, `campaign_unschedule`) correctly use `EmailCampaign.active`.

**Fix sketch**: Change the three `get_object_or_404` calls to:
```python
get_object_or_404(EmailCampaign.active, pk=pk, user=value_request.user)
```

---

### [B-032] Certificate and document generation tasks leave campaign stuck in `'queued'` when all generations fail and `auto_send=True`
- **Discovered**: 2026-06-20
- **Area**: `certificates/tasks.py` (line 41), `documents/tasks.py` (line 104)
- **Severity**: **High** - campaign is permanently stuck in `'queued'` with no email task ever dispatched and no user-visible error
- **Resolved**: 2026-06-20
- **Status**: **Closed**

Both tasks gate the email dispatch on `recipients_created > 0`:
```python
if auto_send and recipients_created > 0:
    campaign.status = 'queued'
    ...
    send_campaign_emails_task.delay(campaign.id)
```

If every recipient's certificate/document generation fails (`recipients_created == 0`), neither the email task is dispatched nor `_finalize_if_complete` is called. The campaign was already set to `'queued'` by `campaign_queue` before the generation task ran, and it stays there indefinitely. All recipients are in `'failed'` state; the stale-sending reaper won't help because no `'sending'` rows exist.

**Fix sketch**: When `auto_send=True` and `recipients_created == 0`, call `_finalize_if_complete(campaign)` (or explicitly set `campaign.status = 'failed'`). At minimum, log a clear error so the operator knows to investigate.

---

### [B-033] `SMTPConfig.save()` deactivates siblings via the unfiltered `objects` manager, touching soft-deleted configs
- **Discovered**: 2026-06-20
- **Area**: `accounts/models.py` (line 105)
- **Severity**: **Low** - silently updates `is_active` on soft-deleted SMTP configs
- **Resolved**: 2026-06-20
- **Status**: **Closed**

```python
SMTPConfig.objects.filter(user=self.user, is_active=True).exclude(pk=self.pk).update(is_active=False)
```

The `objects` manager is unfiltered (includes soft-deleted records). If a user soft-deleted an SMTP config that still had `is_active=True`, activating another config would flip `is_active` on the deleted record as a side effect. This is harmless at runtime (deleted configs are not used) but is noisy and violates the principle of not mutating soft-deleted records.

**Fix sketch**: Replace `SMTPConfig.objects.filter(...)` with `SMTPConfig.active.filter(...)` so only live configs are touched.

---

### [B-034] `LA`-mode images not composited correctly when saving certificates and documents as PDF
- **Discovered**: 2026-06-20
- **Area**: `certificates/utils.py` (line 185), `documents/utils.py` (line 203)
- **Severity**: **Low** - only affects certificate/document template images stored in grayscale+alpha (`LA`) mode; RGBA and RGB images are unaffected
- **Resolved**: 2026-06-20
- **Status**: **Closed**

Both `generate_certificate_pdf_bytes` and `generate_document_bytes` use:
```python
mask = img.split()[3] if img.mode == 'RGBA' else None
bg.paste(img, mask=mask)
```

For `LA` mode (grayscale + alpha), the alpha channel is `split()[1]`, not `split()[3]`. Passing `mask=None` causes PIL to paste without alpha compositing, so transparent areas in the template image are not flattened to white correctly, potentially producing corrupted PDFs for `LA`-mode templates.

**Fix sketch**:
```python
if img.mode == 'RGBA':
    mask = img.split()[3]
elif img.mode == 'LA':
    mask = img.split()[1]
else:
    mask = None
bg.paste(img, mask=mask)
```
Apply the same fix in both files.

---

### [B-035] Double `@login_required` decorator on `document_template_fields`
- **Discovered**: 2026-06-20
- **Area**: `documents/views.py` (lines 147-148)
- **Severity**: **Low** - dead code / copy-paste artifact; functionally harmless
- **Resolved**: 2026-06-20
- **Status**: **Closed**

```python
@login_required
@login_required
def document_template_fields(value_request, pk):
```

The second `@login_required` is redundant and should be removed.

**Fix sketch**: Remove the duplicate decorator on line 147.

---

### [B-023] Null bytes embedded in several Python source files
- **Discovered**: 2026-06-08
- **Area**: `emails/views.py` (29), `emails/forms.py` (32), `certificates/views.py` (29), `certificates/forms.py` (16)
- **Severity**: **Medium** — CPython rejects `\x00` in source (`SyntaxError: source code string cannot contain null bytes`), so these modules fail to import on any host that reads the bytes verbatim (Linux/CI/Docker). Discovered while running the test suite for the signatures feature.
- **Resolved**: 2026-06-20
- **Status**: **Closed**

Each listed file contains embedded NUL bytes (counts above). The app may run today on the author's machine, but the files will not import under a strict reader and break portability/CI. Likely an editor encoding/save artifact (possibly related to the truncation seen in B-021).

**Fix sketch**: Strip NULs and re-save as UTF-8 (no BOM), e.g. `python -c "import pathlib;p=pathlib.Path('emails/views.py');p.write_bytes(p.read_bytes().replace(b'\x00',b''))"` for each file, then run the suite to confirm. Consider adding a pre-commit check that rejects NUL bytes.

---

### [B-022] Campaign forms only show active SMTP configurations in dropdown
- **Discovered**: 2026-06-08
- **Resolved**: 2026-06-08
- **Area**: `emails/forms.py`, `emails/views.py`, `certificates/forms.py`, `certificates/views.py`, `documents/forms.py`, `documents/views.py`
- **Severity**: **Medium**
- **Status**: **Closed**

Campaign forms (standard, certificate, and document) only displayed active SMTP configurations (`is_active=True`) in their dropdowns instead of all user configurations with the active one pre-selected.

**Fix applied**: Updated `EmailCampaignForm`, `EmailCampaignEditForm`, `CertificateCampaignForm`, and `DocumentCampaignForm` to include all of a user's configured SMTP accounts, pre-selecting the active one by default. View validations were updated to check if any SMTP configuration exists for the user instead of specifically requiring an active one.

---

### [B-021] accounts/models.py truncated — SMTPConfig.get_app_password() missing
- **Discovered**: 2026-06-07
- **Resolved**: 2026-06-07
- **Area**: `accounts/models.py` (SMTPConfig) / email send pipeline
- **Severity**: **Critical** — breaks all SMTP sending; encryption test failed
- **Status**: **Closed**

`accounts/models.py` was truncated on disk mid-`set_app_password()` (file ended at
line ~118), so `get_app_password()` and the tail of `set_app_password()` were
missing. `emails/tasks.py` calls `smtp_config.get_app_password()` in the send
path, so every real send would raise `AttributeError`; `test_smtp_config_encryption`
failed for the same reason. Same truncation signature as the `core/urls.py`
corruption seen during an editor/session interruption this date.

**Fix applied**: restored the missing `set_app_password` body line plus the full
`get_app_password()` method (decrypt via Fernet, returns "" + logs on failure).
Full test suite now 45/45. Recommend a quick audit that no other source file was
left truncated (a `py_compile` sweep passed for all project modules).

---

### [B-020] Orphaned account_emailaddress rows block schema migrations
- **Discovered**: 2026-06-07
- **Resolved**: 2026-06-07
- **Area**: `account_emailaddress` table (allauth) / data integrity
- **Severity**: **High** — any schema migration fails on SQLite while present
- **Status**: **Closed**

Three `account_emailaddress` rows referenced users that no longer exist:
`(pk=4, user_id=5, madbrand.dev@gmail.com)`, `(pk=5, user_id=6, devlopersachin007@gmail.com)`,
`(pk=6, user_id=7, devlopersachin007@gmail.com)`. Same root cause as **B-019**:
users 5/6/7 were hard-deleted outside the ORM cascade, orphaning their allauth
records. SQLite rebuilds tables on `ALTER`, running a global `foreign_key_check`
at the end of each `schema_editor` block, so these dangling rows raise
`IntegrityError` and abort migrations (surfaced while applying the SendGlob
Phase 1 document-model migrations).

**Fix applied**: deleted the three orphaned rows (their owning users are gone and
allauth never resolves them). Equivalent ORM cleanup:
```
uv run python manage.py shell -c "
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
User = get_user_model()
orphaned = EmailAddress.objects.exclude(user_id__in=User.objects.values('id'))
print('Deleting', orphaned.count(), list(orphaned.values('id','user_id','email')))
orphaned.delete()
"
```
Recommendation: audit for any remaining orphaned allauth rows and add a guarded
hard-delete path (or ON DELETE CASCADE enforcement) so future user deletions
cannot orphan `account_emailaddress` / `socialaccount_socialaccount` again.

---

### [B-019] Orphaned SocialAccount causes DoesNotExist on Google login
- **Discovered**: 2026-06-07
- **Resolved**: 2026-06-07
- **Area**: `socialaccount_socialaccount` table / `accounts/models.py`
- **Severity**: **Critical** — blocks all Google sign-in for affected UIDs
- **Status**: **Closed**

`SocialAccount` row (id=2, uid=`117600264257583368500`) has `user_id=5` which no
longer exists in `accounts_customuser`. On the OAuth callback, allauth's
`_lookup_by_socialaccount` finds the row then accesses `account.user` → Django
fires `CustomUser.objects.get(pk=5)` → `CustomUser.DoesNotExist`.

Root cause: the `CustomUser` was hard-deleted (likely via admin or `queryset.delete()`)
without the cascade removing the `SocialAccount` first. This should not be possible
with a properly constrained FK, which suggests the deletion bypassed the ORM
(e.g., raw SQL / DB tool / SQLite file replace).

**Required fix**: stop the server, then run:
```
uv run python manage.py shell -c "
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
User = get_user_model()
orphaned = SocialAccount.objects.exclude(user_id__in=User.objects.values('id'))
print('Deleting', orphaned.count(), 'orphaned records:', list(orphaned.values('id','uid','user_id')))
orphaned.delete()
"
```

**Second bug found alongside this**: `CustomUser` with email `devlopersachin007@gmail.com`
(id=8) has `username=''` because allauth social signup calls `user.save()` directly,
bypassing `CustomUserManager.create_user()` where the UUID username is assigned.
Fixed in `CustomUser.save()` (see B-019 code fix below). The existing blank-username
record also needs a one-time repair:
```
uv run python manage.py shell -c "
import uuid
from accounts.models import CustomUser
blank = CustomUser.objects.filter(username='')
for u in blank:
    u.username = uuid.uuid4().hex[:30]
    u.save(update_fields=['username'])
    print('Fixed:', u.email)
"
```

---

## Closed / Resolved

### [B-001] Tracking pixel URL hardcoded to old `/track/` path
- **Discovered**: 2026-06-03
- **Resolved**: 2026-06-04
- **Area**: `emails/tasks.py` (`build_tracking_pixel`)
- **Severity**: **Critical** - silently broke open-rate tracking on every email sent
- **Status**: **Closed**

The tracking pixel `<img>` URL was built with a hardcoded `/track/` literal that 404'd after `emails.urls` was remounted under `/dashboard/`.

**Resolution**: Extracted `build_tracking_pixel(site_url, tracking_uuid)` helper that uses `reverse('emails:email_open_track', kwargs={'tracking_uuid': tracking_uuid})` so the path always reflects the live URL configuration. Hardcoded string removed.

---

### [B-002] `send_campaign_emails` has no concurrency lock on the campaign
- **Discovered**: 2026-06-03
- **Resolved**: 2026-06-04
- **Area**: `emails/tasks.py` (`_begin_campaign_send`, `_claim_recipient_batch`)
- **Severity**: **High** - could cause double-sends if two workers picked up the same campaign
- **Status**: **Closed**

Two concurrent workers could both pass the status check and process the same recipient batch.

**Resolution**: Extracted `_begin_campaign_send` (campaign row lock + status flip) and `_claim_recipient_batch` (recipient claim), both wrapped in `transaction.atomic()` with `select_for_update()`. Recipient claim also uses `select_for_update(skip_locked=True)` when the backend supports it, so a second worker gets a disjoint batch. Lock gracefully degrades to a plain query on SQLite.

---

### [B-003] Recipients stuck in `'sending'` state are silently abandoned
- **Discovered**: 2026-06-03
- **Resolved**: 2026-06-04
- **Area**: `emails/tasks.py` (`reset_stale_sending`, `_finalize_if_complete`, `reap_stale_sending_recipients_task`)
- **Severity**: **High** - campaign could be marked complete with un-sent recipients
- **Status**: **Closed**

Recipients flipped to `'sending'` before SMTP dispatch could be stuck forever if the worker died, and the completion check only tested `pending/retrying`, causing false "completed" status.

**Resolution**: Three-part fix: (1) `_finalize_if_complete` counts `pending + retrying + sending` so a campaign is never marked complete while rows remain in flight; (2) `reset_stale_sending(campaign)` resets recipients in `'sending'` older than 10 minutes back to `'retrying'` at the start of every send batch; (3) `reap_stale_sending_recipients_task` periodic Celery task sweeps globally across all active campaigns.

---

### [B-004] Transient SMTP error marks the entire campaign as `failed`
- **Discovered**: 2026-06-03
- **Resolved**: 2026-06-04
- **Area**: `emails/tasks.py` (`_open_smtp_connection`)
- **Severity**: **High** - one network blip killed a campaign with no auto-recovery
- **Status**: **Closed**

Any `connection.open()` exception immediately set campaign status to `'failed'` with no retry.

**Resolution**: Extracted `_open_smtp_connection` which retries up to `SMTP_CONNECT_MAX_ATTEMPTS` (4) times with exponential backoff (`SMTP_CONNECT_BACKOFF_BASE` = 1 s, doubled each attempt). `SMTPAuthenticationError` is re-raised immediately (terminal). Only after the retry budget is exhausted does the caller mark the campaign `'failed'` and release claimed recipients back to `'retrying'`.

---

### [B-005] `CustomUser.save()` force-equates `username` and `email`, creating redundant unique constraints
- **Discovered**: 2026-06-03 (during Google sign-in IntegrityError investigation)
- **Resolved**: 2026-06-04
- **Area**: `accounts/models.py` (`CustomUser`, `CustomUserManager`)
- **Severity**: **Medium** - root cause of B-018
- **Status**: **Closed**

The `save()` override forced `username = email[:150]`, making both unique columns identical and causing `UNIQUE constraint failed` on any code path that created a second user with the same email.

**Resolution**: Added `CustomUserManager(BaseUserManager)` that assigns a random `uuid.uuid4().hex[:30]` username on every new user, satisfying AbstractUser's unique constraint without coupling it to email. Removed the `save()` override entirely. Set `REQUIRED_FIELDS = []` so `createsuperuser` no longer prompts for username. No schema migration needed — username column is retained but is now meaningless to the application.

---

### [B-006] `get_default_font_path` makes a blocking HTTPS download from a Celery worker
- **Discovered**: 2026-06-03
- **Resolved**: 2026-06-04
- **Area**: `certificates/utils.py` (`get_bundled_font_path`, `_load_scalable_font`)
- **Severity**: **Medium** - caused intermittent failures on first cert generation
- **Status**: **Closed**

Runtime HTTPS download of PlayfairDisplay from GitHub blocked the worker, failed on slow/air-gapped hosts, and silently fell back to Pillow's fixed 11px default font.

**Resolution**: Removed the runtime download entirely. `get_bundled_font_path()` looks for a vendored `.ttf` under `static/fonts/`. Fall
---

### [B-036] Test suite: documents views that call `task.delay()` fail without Redis broker
- **Discovered**: 2026-06-21 (during certificate deprecation test run)
- **Area**: `documents/tests.py`, `documents/tests_signatures.py`
- **Severity**: **Low** - only affects test environment without Redis
- **Resolved**: 2026-06-22
- **Status**: **Closed**

Four tests hit views that call `generate_documents_task.delay()` or `process_signature_task.delay()` without mocking the Celery task dispatch. In a sandbox without Redis, Kombu raises `AttributeError: 'NoneType' object has no attribute 'Redis'` before the view completes.

**Resolution**: Already addressed by the test settings. `core/settings.py` sets `CELERY_TASK_ALWAYS_EAGER = True` under the test configuration, so `task.delay()` runs inline and never touches the broker. Verified: all four affected tests pass on Python 3.13 with no Redis available.

Affected tests:
- `documents.tests.DocumentUITests.test_create_document_campaign_generates_documents`
- `documents.tests_signatures.SignatureViewTests.test_upload_creates_and_processes_signature`
- `documents.tests_signatures.SignatureViewTests.test_upload_rejects_non_signature_without_creating_record`
- `documents.tests_signatures.SignatureEditViewTests.test_reprocess_restarts_processing`

**Fix**: Add `@patch('documents.tasks.generate_documents_task.delay')` / `@patch('documents.tasks.process_signature_task.delay')` decorators to the affected tests, or add `@override_settings(CELERY_TASK_ALWAYS_EAGER=True)` to the test classes.

---

### [B-037] Test suite: `test_non_signature_image_is_rejected` does not raise `SignatureProcessingError`
- **Discovered**: 2026-06-21 (during certificate deprecation test run)
- **Area**: `documents/tests_signatures.py` (`PipelineTests`)
- **Severity**: **Low** - test-only, pre-existing
- **Resolved**: 2026-06-22
- **Status**: **Closed**

`PipelineTests.test_non_signature_image_is_rejected` (and the downstream `SignatureViewTests.test_upload_rejects_non_signature_without_creating_record`, which returned 302 instead of 200) calls the pipeline with a busy non-signature image and expects `SignatureProcessingError`. The photo guard in `documents/signature_processing.py` did not fire because the threshold was too low.

**Resolution**: Raised `SignatureConfig.min_paper_fraction` from `0.08` to `0.5`. The photo guard rejects an upload whose single most-common (coarsely quantized) colour bin covers less than this fraction. Measured separation is wide and stable: a clean signature scores ~0.95 paper, a tinted/ruled-paper signature ~0.93, while a busy photo scores ~0.16. The new threshold sits at the midpoint of that gap, so genuine signatures on used or unevenly lit paper still pass while photos/selfies/screenshots are turned away with a clear message. Both tests now pass; full suite 80/80 green.

---

### [B-041] Physical file cleanup: duplicated logic, not transaction-safe, and two models never purged
- **Discovered**: 2026-06-22 (file-storage consistency audit)
- **Area**: `core/file_cleanup.py` (new), `certificates/models.py`, `emails/models.py`, `documents/models.py`, `core/tasks.py`
- **Severity**: **Medium** - storage leak plus a historical-accuracy risk on rollback
- **Resolved**: 2026-06-22
- **Status**: **Closed**

The cleanup signal handlers were copy-pasted in three apps (single-source-of-truth violation), deleted files inside the transaction (a rollback could leave a record pointing at a missing file), and ran an extra query on every save. Separately, soft-deleted `Signature` and `CertificateTemplate` records were never physically purged, so their files accumulated on disk indefinitely.

**Resolution**: Centralized cleanup in `core/file_cleanup.py` with one `register_file_cleanup(*models)` helper. Deletions are deferred via `transaction.on_commit`, and `pre_save` early-exits when `update_fields` touch no file field. The three apps now call the helper instead of defining handlers. The purge task gained `_purge_by_retention` and now reclaims soft-deleted `Signature` files past retention, tracked by a new `PurgeLog.signatures_purged` counter (migration `core/0003`). `CertificateTemplate` is intentionally left out: the certificates app is deprecated and migration `core/0002` already removed its purge counter. Added 5 cleanup tests and 2 signature-purge tests; full suite 108/108 green. See `docs/file_storage_cleanup_plan.md`.

---

## Technical Debt (file-storage consistency)

### [B-042] No reconciliation tool for files already orphaned on disk
- **Discovered**: 2026-06-22
- **Area**: `core/management/commands/find_orphan_media.py`
- **Severity**: **Low**
- **Resolved**: 2026-06-23
- **Status**: **Closed**

Nothing reconciled `MEDIA_ROOT` against the database, so files orphaned before the signals existed (or by any bulk `update()` that bypasses `pre_save`) were invisible. **Resolution**: Added the `find_orphan_media` command (Phase 4). It walks `MEDIA_ROOT`, builds the referenced-file set from every `FileField`/`ImageField` on every installed model using each model's `_base_manager` (unfiltered, so retained soft-deleted files still count as referenced), and reports files with no referencing row. Dry-run by default; `--delete` removes them and `--older-than DAYS` skips recently written files. Tests in `core/tests.py` (`FindOrphanMediaCommandTests`) cover dry-run reporting, referenced/soft-deleted retention, deletion, and the age filter, each with an isolated per-test `MEDIA_ROOT`. Full suite green.

### [B-043] Redundant manual file deletes in views now duplicate the signal
- **Discovered**: 2026-06-22
- **Area**: `emails/views.py`, `documents/views.py`
- **Severity**: **Low**
- **Resolved**: 2026-06-23
- **Status**: **Closed**

Several views called `file.delete(save=False)` right before a physical delete that the centralized `post_delete` signal also handles, duplicating logic. **Resolution**: Removed the redundant manual deletes in three `emails/views.py` paths (attachment deletion on edit, recipient `replace` import, and document-template-change regeneration) and one in `documents/views.py` (template-edit regeneration). In the record-delete cases (`CampaignAttachment.delete()`, `Recipient` queryset `delete()`) the `post_delete` signal reclaims the file; in the field-clear cases (`document_file = None; save()`) the `pre_save` signal removes the replaced file. The one manual delete in `campaign_restart` is intentionally kept because it precedes a bulk `QuerySet.update()`, which does not fire `pre_save`/`post_delete`. Phase 5 complete; full suite green.

---

## Soft delete and purge reassessment (2026-06-23)

See `docs/soft_delete_purge_reassessment_plan.md` for the full audit.

### [B-044] Soft-deleted draft campaigns are never purged (storage and DB leak)
- **Discovered**: 2026-06-23 (soft-delete/purge reassessment)
- **Area**: `core/tasks.py` (`purge_soft_deleted_records`)
- **Severity**: **Medium** - abandoned drafts leak forever and pin their template/SMTP/document-template from purging
- **Resolved**: 2026-06-23
- **Status**: **Closed**

`purge_soft_deleted_records` only hard-deleted campaigns whose status was in `PURGEABLE_CAMPAIGN_STATUSES = {completed, failed, cancelled}`. But `campaign_delete` permits deleting a `draft` campaign, which keeps `status='draft'` and only sets `deleted_at`. The purge never touched it, so its `Recipient` rows, `CampaignAttachment` files, and generated `document_file`s were never reclaimed. Worse, the soft-deleted draft still counted in `_purge_unreferenced`'s unfiltered `campaigns.exists()` check, so the `EmailTemplate`, `DocumentTemplate`, and `SMTPConfig` it referenced could never become unreferenced and never purged either. Deleting a draft is the most common delete path, so this leaked routinely.

**Resolution**: Made `EmailCampaign.LOCKED_STATUSES` the single source of truth for purge eligibility. The campaign query is now `EmailCampaign.objects.filter(deleted_at__lt=cutoff).exclude(status__in=EmailCampaign.LOCKED_STATUSES)`, so any soft-deleted campaign that is not in-flight or scheduled (queued/sending/scheduled) is reclaimed once retention elapses; abandoned drafts purge and free their referenced template/SMTP config on the same run. Removed the now-dead `PURGEABLE_CAMPAIGN_STATUSES` constant. Added `core.tests.PurgeSoftDeletedRecordsTests.test_soft_deleted_draft_campaign_is_purged` and `test_draft_purge_frees_referenced_template_and_smtp`, plus the `emails.tests` `test_soft_deleted_campaign_actions_return_404` guardrail (Phase 3); the existing `test_active_state_campaign_is_never_purged` (queued/sending/scheduled) still holds. Full suite 123/123 green (`uv run python manage.py test`, 2026-06-23).

Residual (tracked): a soft-deleted `scheduled` campaign past retention is still not purged (storage-only; the scheduler filters `deleted_at__isnull=True`, so it never fires). Low priority.

### [B-045] `SoftDeleteModel.restore()` can violate the Signature partial-unique constraint
- **Discovered**: 2026-06-23
- **Area**: `core/models.py` (`restore`), `documents/models.py` (`Signature`)
- **Severity**: **Low** (latent - `restore()` had no Python call sites yet)
- **Resolved**: 2026-06-23
- **Status**: **Closed**

`Signature` has `UniqueConstraint(fields=['user','name'], condition=deleted_at IS NULL)`. `restore()` did a blind `deleted_at=None` save with no guard, so restoring a signature whose name was reused by a new active record would raise a raw `IntegrityError`. **Resolution**: Overrode `Signature.restore()` to raise a clear `ValidationError` when an active sibling already holds the same `(user, name)`, delegating to `super().restore()` otherwise. The generic `SoftDeleteModel.restore()` is unchanged (only `Signature` carries the partial-unique constraint). Added `test_restore_when_name_is_free_succeeds` and `test_restore_blocked_when_active_name_clashes` to `documents.tests_signatures.SignatureModelTests`.

### [B-046] Purge does O(N) per-row deletes plus an N+1 reference check
- **Discovered**: 2026-06-23
- **Area**: `core/tasks.py`
- **Severity**: **Low** (informational)
- **Resolved**: 2026-06-23
- **Status**: **Closed**

The purge iterated rows and called `obj.hard_delete()` per row, with a per-row `campaigns.exists()` in `_purge_unreferenced` (an N+1 on the reference check). **Resolution**: Replaced the per-row `exists()` with a single `annotate(Count('campaigns'))` so eligibility is decided in one query (`Count('campaigns')` uses the reverse accessor shared by all three protected models, preserving the unfiltered "referenced by any campaign" semantics). The per-row `hard_delete()` loop is intentionally retained: it is required to fire cascade and file-cleanup signals and to catch `ProtectedError`, and the purge is a weekly retention-bounded job. Revisit with batching/chunking only if the soft-deleted backlog grows very large.

---

## Security

### [B-047] Sensitive media (signatures, generated documents) is served without authentication or authorization
- **Discovered**: 2026-06-23 (signatures security review)
- **Area**: `core/urls.py` (media serving), `core/settings.py` (`MEDIA_URL`/`MEDIA_ROOT`), `documents` (`Signature.original_image`/`processed_image`, `Recipient.document_file`), templates that render `*.url`
- **Severity**: **High** - broken access control on sensitive files (a user's signature image)
- **Resolved**: 2026-06-23
- **Status**: **Closed** (one residual tracked as B-048)

The app layer scopes every signature and document view by owner (`get_object_or_404(Signature.active, pk=pk, user=request.user)`; `resolve_signatures` filters `user=...`), so in-app access control is correct. But the files themselves were stored under `MEDIA_ROOT` and exposed at predictable public URLs (`/media/signatures/originals/<name>.png`, `/media/signatures/processed/<name>.png`, `/media/generated_documents/YYYY/MM/DD/doc_<recipient_id>_<name>_<8hex>.<ext>`). With `DEBUG=True` Django's `static()` serves `/media/` to anyone; with `DEBUG=False` a front web server typically serves `/media/` publicly as well. There was no authenticated `FileResponse` view checking ownership before returning the bytes, so the in-app authorization was bypassed by fetching the media URL directly, and signature filenames were guessable (Django only appends a random suffix on collision).

**Resolution** (full hardening): (1) Sensitive files are now served only through authenticated, ownership-checked views: `documents.views.signature_image` (variant whitelisted to original/processed, `Signature.active` scoped to the user) and `emails.views.recipient_document` (`Recipient` scoped to `campaign__user`). Templates link to these named URLs instead of `FieldFile.url`. (2) Stored filenames are randomized via `upload_to` callables (`signatures/<dir>/<hex><ext>`, `generated_documents/YYYY/MM/DD/<hex><ext>`), removing guessable names and recipient PII from paths (migrations `documents/0008`, `emails/0011`). (3) Deleting a signature now removes the image files from storage immediately (soft-deleted row retained for name-history/audit, file fields cleared) instead of holding them for the 90-day purge window. (4) `core/urls.py` documents that production must not serve the `signatures/`/`generated_documents/` subtrees publicly; see `docs/media-security.md`. Tests: `documents.tests_signatures.SignatureImageServingTests` (owner 200, other-user 404, bad variant 404, anonymous redirect, randomized paths, delete-removes-files) and `emails.tests.RecipientDocumentServingTests` (owner 200, other-user 404, anonymous redirect, randomized path).

### [B-048] Signature images are not encrypted at rest
- **Discovered**: 2026-06-23 (signatures security review)
- **Area**: `documents` (`Signature.original_image`/`processed_image`), `core/storage.py`, `core/settings.py`
- **Severity**: **Low/Medium** (depends on storage threat model)
- **Resolved**: 2026-06-23
- **Status**: **Closed** (residual tracked as B-049)

SMTP passwords are encrypted with Fernet, but signature images were stored as plaintext files on disk. Access control and unguessable paths are handled (B-047); encryption at rest additionally protects against a storage-level compromise or backup leak. **Resolution**: Added `core.storage.EncryptedFileSystemStorage`, a `FileSystemStorage` subclass that Fernet-encrypts contents on `_save` and decrypts on `_open`, keyed by the new `FILE_ENCRYPTION_KEY` setting (defaults to `SMTP_ENCRYPTION_KEY` so it works without extra config, rotatable independently). The two `Signature` image fields use it via a callable `storage=encrypted_media_storage` (migration `documents/0009`). Encryption is transparent to the processing task, renderer, and serving views because they all read through `FieldFile.open()`; it relies on B-047 ensuring these files are never served by a static web server (which would return ciphertext). Added the idempotent `encrypt_signature_files` management command to encrypt pre-existing plaintext files in place, and `documents.tests_signatures.SignatureEncryptionAtRestTests` verifying on-disk bytes are ciphertext while `open()` round-trips to the original PNG. See `docs/media-security.md`.

### [B-049] `DocumentTemplate.template_file` is still served via the public media URL
- **Discovered**: 2026-06-23
- **Area**: `documents` (`DocumentTemplate.template_file`), `templates/documents/document_form.html`
- **Severity**: **Low** (template backgrounds are less sensitive than signatures/documents)
- **Resolved**: 2026-07-04
- **Status**: **Closed**

Certificate/document template background images were still referenced by `template_file.url` (e.g. the editor's `loadBg(...)`) and served from the public `/media/` path, unlike signatures and generated documents which go through ownership-checked views (B-047).

**Resolution**: Added `documents.views.document_template_background`
(`documents:template_background`, `/documents/templates/<pk>/background/`),
which resolves `DocumentTemplate.active` scoped to `user=request.user` and
streams via `FileResponse`, matching the B-047 pattern. The editor template
(`document_form.html`) now loads the background from this URL instead of
`template_file.url`. Production guidance updated: `document_templates/` joined
the do-not-serve-publicly subtrees in `core/urls.py` and
`docs/media-security.md`. Encryption at rest was intentionally not applied
(low-sensitivity backgrounds). Tests: `documents.tests.TemplateBackgroundServingTests`
(owner 200, other-user 404, anonymous redirect, soft-deleted 404).
