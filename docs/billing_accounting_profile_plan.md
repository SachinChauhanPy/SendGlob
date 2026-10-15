# SendGlob - Billing, Accounting, and User Profile Plan

**Status:** Proposed
**Date:** 2026-06-30
**Supersedes:** `billing.md` and `docs/billing_implementation_plan.md` (the older
per-email credit model). Those are retained for history only. This plan is the
source of truth going forward.

**Model:** Pay-as-you-go credits + optional monthly plans. The billable unit is a
**generated document**, not an email. Email delivery is free because mail is sent
from the user's own Gmail. AI template generation carries a small credit cost
because it consumes a metered external API.

**Goal:** Sustainability first. The system must reliably meter usage, collect
payment, keep clean financial records, and let the operator see whether monthly
revenue covers infrastructure cost. It is not built for venture-scale billing
complexity.

---

## 1. Design decisions (locked)

1. **Credit unit = one generated document.** Generating a personalized document
   (certificate, letter, ID card) costs 1 credit whether or not it is then
   emailed. Delivery is free.
2. **Email send = 0 credits.** Mail leaves the user's own Gmail and is capped by
   Gmail anyway, so it is not a billable action.
3. **AI template generation = small credit cost** (default 2 credits, configurable).
   It hits a paid/limited model API, so it is metered.
4. **No GST / tax invoicing yet.** The operator is not a registered business.
   The system issues plain, sequentially numbered **payment receipts** marked
   "This is not a tax invoice." All tax fields are modeled but optional, so GST
   invoicing can be switched on later with no schema rebuild.
5. **Financial records are append-only and never soft-deleted or purged.** This
   is a deliberate exception to the project-wide soft-delete invariant. Ledgers,
   payments, and receipts are immutable legal/financial history.
6. **Gateway: Razorpay** (INR for India, USD international). Start in test mode
   and with a manual top-up path so willingness-to-pay can be validated before
   the full integration is finished.

---

## 2. Scope

In scope:

- User profile and billing identity (who is paying, where they are).
- Credit wallet and an append-only credit ledger.
- Per-document and per-AI metering with worker-safe deduction.
- Credit packs (one-time) and monthly plans (recurring).
- Razorpay checkout and webhook handling.
- Plain payment receipts with a forward path to GST invoices.
- Feature gating by tier (free vs paid).
- Internal accounting: revenue reporting and reconciliation against the
  infrastructure cost floor.

Out of scope for this phase:

- GST tax invoices, TDS, e-invoicing (deferred until the business is registered).
- Multi-currency beyond INR/USD.
- Dunning automation beyond a basic past-due state.

---

## 3. App and module layout

A new Django app `billing/`. Profile identity fields that are not billing-specific
live in `accounts/` (extending the existing `CustomUser`), so `accounts` stays the
owner of identity and `billing` owns money.

```
billing/
├── apps.py
├── models.py            # wallet, ledger, packs, plans, subscription, payment, receipt, billing profile, webhook log
├── credit_costs.py      # single source of truth for action -> credit cost
├── wallet.py            # debit/credit service, select_for_update, InsufficientCreditsError
├── services.py          # Razorpay order/subscription creation + signature verification
├── webhooks.py          # idempotent webhook handlers (one per event type)
├── receipts.py          # sequential receipt numbering + PDF rendering
├── accounting.py        # revenue rollups, reconciliation, cost-floor report
├── gating.py            # decorators + mixin for feature/credit gating
├── context_processors.py# inject balance + plan into templates
├── signals.py           # create wallet + free grant on user creation
├── admin.py
├── views.py             # profile, billing dashboard, checkout, receipts, manage plan
├── urls.py
├── management/commands/  # setup_billing_plans, grant_credits, reconcile_payments, revenue_report
└── tests*.py
```

---

## 4. User profile and billing identity

### 4.1 Profile fields (accounts/CustomUser)

`CustomUser` currently holds only `email` and `timezone`. Add general profile
identity:

| Field | Type | Notes |
|---|---|---|
| `full_name` | CharField, blank | Display and receipt name |
| `organization` | CharField, blank | Institute / company (optional) |
| `phone` | CharField, blank | Optional contact |
| `country` | CharField(2), blank | ISO code; also drives INR vs USD pricing instead of IP guessing |

`country` replaces the current IP-header guess in `core/pricing.py` as the
authoritative pricing region once set, falling back to the existing detection when
blank. This removes a long-standing fragility (header-based region detection).

### 4.2 BillingProfile (billing app)

One-to-one with the user. Holds the billing address and the optional tax identity.
Captured at first checkout, editable from the profile page.

```python
class BillingProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='billing_profile')
    billing_name = models.CharField(max_length=200, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True)
    tax_id = models.CharField(max_length=30, blank=True)   # GSTIN later; blank for now
    updated_at = models.DateTimeField(auto_now=True)
```

The billing details are **snapshotted onto each receipt** at purchase time, so
later edits never rewrite historical documents (historical-accuracy invariant).

---

## 5. Credit data model

All money/credit models are plain `models.Model` (not `SoftDeleteModel`) and are
excluded from the weekly purge.

### 5.1 CreditWallet

Authoritative balance, one row per user. Never mutated directly outside `wallet.py`.

```python
class CreditWallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='wallet')
    balance = models.IntegerField(default=0)
    lifetime_earned = models.IntegerField(default=0)
    lifetime_spent = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
```

### 5.2 CreditTransaction (append-only ledger)

Every movement, positive or negative, is one immutable row. Balance is derived by
applying transactions; `balance_after` is a snapshot for audit only.

```python
class CreditTransaction(models.Model):
    TYPE_SIGNUP_GRANT = 'signup_grant'
    TYPE_PACK_PURCHASE = 'pack_purchase'
    TYPE_PLAN_GRANT = 'plan_grant'
    TYPE_DEBIT_DOCUMENT = 'debit_document'
    TYPE_DEBIT_AI = 'debit_ai'
    TYPE_ROLLOVER = 'rollover'
    TYPE_REFUND = 'refund'
    TYPE_ADMIN_ADJUSTMENT = 'admin_adjustment'
    # ... choices ...

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='credit_transactions')
    transaction_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    amount = models.IntegerField()          # +credit / -debit
    balance_after = models.IntegerField()
    reference_id = models.CharField(max_length=200, blank=True)  # payment id / campaign id
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'created_at'])]
```

### 5.3 CreditPack, Plan, Subscription, Payment, Receipt, WebhookEvent

- **CreditPack** - admin-managed one-time bundles: `name, credits, price,
  currency, is_active, display_order`.
- **Plan** - recurring tiers: `tier, billing_cycle, currency, price,
  credits_per_cycle, razorpay_plan_id`, plus feature flags
  (`recurring_scheduling, max_smtp_configs, full_analytics, priority_support`).
- **Subscription** - user's plan state: `plan, status (active/cancelled/past_due/
  expired/pending), razorpay_subscription_id, current_period_start/end,
  cancel_at_period_end`.
- **Payment** - one row per gateway payment: `payment_type (pack|plan),
  razorpay_payment_id (unique), razorpay_order_id, amount, currency, status
  (captured/failed/refunded), credits_granted, paid_at`. Created only by the
  webhook handler, never by the UI.
- **Receipt** - see section 7.
- **WebhookEvent** - idempotency store: `event_id (unique), event_type, payload,
  processed_at`. Always store, then process.

---

## 6. Credit costs and deduction

### 6.1 Single source of truth

```python
# billing/credit_costs.py
DOCUMENT_GENERATION = 1   # per document, delivery included free
AI_TEMPLATE_GENERATION = 2
EMAIL_SEND = 0
```

### 6.2 Worker-safe deduction

Deduction happens inside the Celery tasks that do the work
(`documents/tasks.py` for generation, `emails/ai.py` for AI), never in the view.
`wallet.debit()` runs in a transaction with `select_for_update()` on the wallet
row to prevent two concurrent workers overdrawing.

Rules:

- **Check before work, debit per unit produced.** For a batch of N documents,
  verify `balance >= estimated cost` before starting, then debit 1 credit as each
  document is successfully generated. If a document fails, no credit is charged
  for it (the failure path already exists; hook the debit to the success path).
- **Insufficient credits** raises `InsufficientCreditsError`; the task records a
  clear per-recipient reason and the campaign surfaces "stopped: out of credits"
  rather than failing silently. This reuses the existing per-recipient failure
  reason mechanism.
- **Refund on system failure.** If the platform (not the user) causes a failure
  after debit, credit it back with `TYPE_REFUND` referencing the original
  transaction.

### 6.3 Free tier grant

On user creation a signal creates the wallet and grants a one-time free balance
(default 50 credits, configurable) with `TYPE_SIGNUP_GRANT`. Free tier also caps
recipients-per-run and disables scheduling/AI via gating (section 9).

---

## 7. Accounting layer (receipts + bookkeeping, no GST yet)

### 7.1 Receipt

Issued automatically when a Payment is captured.

```python
class Receipt(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT,
                                   related_name='receipt')
    number = models.CharField(max_length=30, unique=True)   # e.g. SG-2026-000123
    issued_at = models.DateTimeField(auto_now_add=True)
    # snapshot of billing identity at purchase time
    billing_name = models.CharField(max_length=200, blank=True)
    billing_address = models.TextField(blank=True)
    billing_country = models.CharField(max_length=2, blank=True)
    # money snapshot
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    # tax fields modeled now, unused until GST registration
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_id_seller = models.CharField(max_length=30, blank=True)   # seller GSTIN later
    is_tax_invoice = models.BooleanField(default=False)
    pdf = models.FileField(upload_to='receipts/', blank=True)
```

- **Numbering** is sequential and gap-free, generated under a row lock or a
  dedicated counter so concurrent payments cannot collide or skip numbers.
- Until GST registration, `is_tax_invoice=False`, `tax_*` are zero, and the PDF
  footer reads "This is not a tax invoice." Switching on GST later means: set the
  seller GSTIN, compute CGST/SGST/IGST from buyer vs seller state, flip
  `is_tax_invoice=True`. No migration required.
- PDF is rendered with the existing PDF tooling and stored through an
  authenticated, ownership-checked view (reuse the media-security pattern already
  used for signatures and generated documents). Receipts are not public URLs.

### 7.2 Internal bookkeeping and the cost floor

The operator's real question is "are we covering the server this month." Provide:

- **`revenue_report` management command** and an admin page: revenue captured this
  month and trailing months, broken down by packs vs plans and by currency, count
  of paying users, and the **net against a configurable monthly cost floor**
  (`INFRA_COST_FLOOR` setting). Output is a simple "above / below floor by X" line.
- **`reconcile_payments` command:** cross-check `WebhookEvent` -> `Payment` ->
  `Receipt` and flag any payment without a receipt, any captured Razorpay payment
  missing locally, and any refund not reflected in the ledger.
- All figures come from immutable `Payment`/`CreditTransaction` rows, so reports
  are reproducible and auditable.

---

## 8. Razorpay integration

- `services.py` isolates every Razorpay call. Packs use **Orders** (one-time);
  plans use **Subscriptions** + UPI Autopay.
- Checkout flow: view creates an Order/Subscription -> returns id to the Razorpay
  checkout widget -> on success the client posts back -> **server verifies the
  signature** -> the authoritative crediting happens in the **webhook**, not the
  client callback (never trust the browser for crediting).
- `webhooks.py`: verify `X-Razorpay-Signature`, store the `WebhookEvent` (dedup on
  event id), then process. Handlers: `payment.captured`, `payment.failed`,
  `subscription.charged`, `subscription.cancelled`, `refund.processed`. Each is
  idempotent.
- **Unregistered-business reality (flagged):** a personal/PAN Razorpay account can
  accept payments but with KYC and possible volume limits, and you may only issue
  non-tax receipts. Keep settlement to a personal/current account, track income for
  personal tax, and plan to register a business before scaling collections. This
  is a compliance note for the operator, not a code task.

---

## 9. Feature gating

A user's tier is derived: active `Subscription` -> that plan; else if they have ever
purchased a pack -> "paid PAYG"; else "free."

- `@require_credits(action)` decorator / `wallet.can_afford()` guards the
  generation and AI entry points (backend authoritative, per project rule).
- `PlanFeatureRequiredMixin` guards plan-only features (recurring scheduling,
  multiple SMTP configs, full analytics).
- Free-tier limits (recipients-per-run, single template, no scheduling/AI) are
  enforced server-side; the UI mirrors them but never substitutes for the backend
  check.
- `context_processors.py` injects `balance`, `tier`, and `low_balance` so every
  page can show the wallet and an upgrade nudge.

---

## 10. UI surfaces (theme-aware, Bootstrap 5)

All pages support light/dark using theme-aware utilities (`bg-body`,
`text-body`, `border-secondary`), never hardcoded colors.

- **Profile page** (`accounts:settings` extended): identity, timezone (existing),
  country, and the billing profile (address, optional tax id).
- **Billing dashboard** (`billing:dashboard`): current balance, current tier,
  credits used this month, low-balance warning, buttons to buy credits / manage
  plan.
- **Buy credits** (`billing:packs`) and **Plans** (`billing:plans`): region-aware
  pricing from `country`.
- **History** (`billing:history`): paginated credit transactions and a list of
  receipts with download links (authenticated view).
- **Checkout success/failure** pages and a **manage subscription** page
  (cancel at period end).

---

## 11. Migrations and data integrity

- New `billing` app migration for all tables; `accounts` migration for the profile
  fields (nullable/blank, safe on existing rows).
- A data migration backfills a `CreditWallet` (with the free grant) for every
  existing user so no account is left wallet-less.
- Financial tables are explicitly excluded from `core/tasks.py` purge and are not
  `SoftDeleteModel` subclasses. Add a guardrail test asserting the purge never
  touches `Payment`, `CreditTransaction`, or `Receipt`.

---

## 12. Testing plan

- **Ledger:** debit/credit correctness, `balance_after` snapshots, refund
  reversal, admin adjustment.
- **Concurrency:** simulate two workers debiting the same wallet; assert no
  overdraft (relies on `select_for_update`).
- **Metering:** document generation debits exactly 1 per successful document and 0
  per failure; AI debit; email send is free.
- **Insufficient credits:** generation stops cleanly, campaign shows the reason,
  no partial corruption.
- **Webhooks:** signature verification, idempotency (same event twice -> one
  credit grant), pack vs subscription crediting.
- **Receipts:** sequential gap-free numbering under concurrent captures; billing
  snapshot is frozen; PDF served only to the owner.
- **Gating:** free-tier limits enforced server-side; plan features blocked without
  an active plan.
- **Purge guardrail:** financial records survive the soft-delete purge.

Celery runs synchronously in tests (existing setup), so no broker is needed.

---

## 13. Phased execution order

**Phase 1 - Wallet, profile, manual validation (no gateway).**
Profile fields, `CreditWallet`, `CreditTransaction`, free grant signal,
per-document/AI metering and enforcement, billing dashboard (read-only balance),
and a `grant_credits` admin command. This lets you sell a pack manually (you add
credits on receiving payment) and prove people actually pay before integrating a
gateway. Lowest risk, fastest to real signal.

**Phase 2 - Razorpay packs + receipts.**
Orders, checkout, webhook crediting, Payment + Receipt with sequential numbering
and PDF. One-time PAYG fully self-serve.

**Phase 3 - Plans + feature gating.**
Subscriptions, recurring grants with capped rollover, tier-based gating across
scheduling/SMTP/analytics.

**Phase 4 - Accounting + reconciliation.**
`revenue_report` against the cost floor, `reconcile_payments`, admin financial
views. GST switch-on path documented but dormant until registration.

---

## 14. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Concurrent overdraft of credits | High | `select_for_update()` on wallet; atomic debit |
| Crediting from client callback (fraud) | High | Credit only in verified webhook |
| Duplicate webhook double-credits | Medium | `WebhookEvent` idempotency store |
| Receipt number gaps/collisions | Medium | Locked counter; gap-free test |
| Financial record accidentally purged | High | Exclude from purge; guardrail test |
| Unregistered-business payment limits | Medium | Start small, personal KYC, register before scaling; receipts not tax invoices |
| Lumpy/seasonal revenue vs fixed cost | Medium | Keep infra cheap; cost-floor report surfaces shortfall early |

---

## 15. Open questions for later

- Should plain email-only campaigns ever be metered if a user abuses them as a
  free bulk sender? (Currently free; revisit if abused.)
- Credit expiry policy for plan rollover (capped at one cycle proposed).
- When to register the business and enable GST invoicing (triggers the dormant
  tax path in `Receipt`).
