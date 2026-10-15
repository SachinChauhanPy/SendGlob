# SendGlob — Billing Module Implementation Plan

**Scope:** Billing only (Phase 4 of the main plan, fully expanded)
**Date:** 2026-06-07
**Model:** Pay As You Grow — Credits (PAYG) + Optional Subscriptions + Module Selection
**Gateway:** Razorpay (INR for India, USD for global via Razorpay International)

---

## Table of Contents

1. [Billing Model Summary](#1-billing-model-summary)
2. [App Structure](#2-app-structure)
3. [Database Models](#3-database-models)
4. [Credit Economics](#4-credit-economics)
5. [Razorpay Integration](#5-razorpay-integration)
6. [Service Layer](#6-service-layer)
7. [Webhook Handler](#7-webhook-handler)
8. [Feature Gating](#8-feature-gating)
9. [Module Selection](#9-module-selection)
10. [Upgrade Nudge System](#10-upgrade-nudge-system)
11. [Views and URL Structure](#11-views-and-url-structure)
12. [Credit Deduction in Send Pipeline](#12-credit-deduction-in-send-pipeline)
13. [Admin Panel](#13-admin-panel)
14. [Settings and Environment](#14-settings-and-environment)
15. [Dependencies](#15-dependencies)
16. [Data Seeding](#16-data-seeding)
17. [Migration Strategy](#17-migration-strategy)
18. [Testing Plan](#18-testing-plan)
19. [Risk Register](#19-risk-register)
20. [Execution Order](#20-execution-order)

---

## 1. Billing Model Summary

### Philosophy

SendGlob serves two distinct usage patterns:

**Event-based users** (webinar organizers, training institutes running seasonal batches) — they need to pay per event, not per month. A recurring subscription they can't use during off-months is money wasted. They will churn.

**Regular users** (institutes with monthly batches, marketing teams, NGOs with quarterly programs) — they benefit from predictable monthly credits at a discount vs buying packs each time.

The model serves both without forcing either into the wrong bucket.

### The "Pay As You Grow" Structure

```
Free Tier
  └── 100 one-time credits on signup
  └── Hard limits: 50 recipients/campaign, 1 template each
  └── No scheduling, no AI generation

PAYG Credit Packs (one-time purchase, credits never expire)
  └── For event-based and infrequent users
  └── Razorpay Order (not subscription)
  └── Unlocks full platform at per-credit pricing

Monthly Subscriptions (auto-renew via Razorpay Subscription + UPI Autopay)
  └── For regular, predictable users
  └── Credits refreshed at start of each billing cycle
  └── Unused credits from previous cycle roll over (capped at 1 cycle worth)
  └── Unlocks premium features PAYG packs cannot access
```

### Credit Pricing

**INR (India):**

| Pack | Credits | Price | ₹/credit |
|---|---|---|---|
| Trial | 300 | ₹149 | ₹0.50 |
| Basic | 1,000 | ₹399 | ₹0.40 |
| Standard | 3,000 | ₹999 | ₹0.33 |
| Pro | 8,000 | ₹2,199 | ₹0.27 |
| Business | 20,000 | ₹4,499 | ₹0.22 |

**INR Monthly Subscriptions:**

| Plan | Credits/month | Price/month | Annual (–25%) |
|---|---|---|---|
| Starter | 1,500 | ₹349 | ₹3,149/yr |
| Growth | 5,000 | ₹899 | ₹8,099/yr |
| Pro | 15,000 | ₹2,199 | ₹19,799/yr |

**USD (Global):**

| Pack | Credits | Price |
|---|---|---|
| Trial | 300 | $3.99 |
| Basic | 1,000 | $9.99 |
| Standard | 3,000 | $24.99 |
| Pro | 8,000 | $54.99 |
| Business | 20,000 | $109.99 |

**USD Monthly Subscriptions:**

| Plan | Credits/month | Price/month | Annual (–25%) |
|---|---|---|---|
| Starter | 1,500 | $9 | $81/yr |
| Growth | 5,000 | $22 | $198/yr |
| Pro | 15,000 | $55 | $495/yr |

### Credit Cost Per Action

| Action | Credits consumed |
|---|---|
| Send 1 email | 1 |
| Generate 1 document (no email delivery) | 1 |
| Generate 1 document + deliver via email | 2 |
| AI email template generation | 5 |
| Scheduled campaign trigger | 0 (no extra cost) |

### Feature Gate by Billing Tier

| Feature | Free | PAYG Pack | Starter Sub | Growth Sub | Pro Sub |
|---|---|---|---|---|---|
| Email campaigns | ✅ 50 recipients | ✅ unlimited | ✅ | ✅ | ✅ |
| Document generation | ✅ 50 per run | ✅ | ✅ | ✅ | ✅ |
| Custom fonts | ❌ | ✅ | ✅ | ✅ | ✅ |
| One-time scheduling | ❌ | ❌ | ✅ | ✅ | ✅ |
| Recurring scheduling | ❌ | ❌ | ❌ | ✅ | ✅ |
| Multiple SMTP configs | ❌ | ❌ | ❌ | ✅ (3) | ✅ (5) |
| AI generation | ❌ | ✅ (5 credits/gen) | ✅ | ✅ | ✅ |
| Analytics (full) | ❌ | Basic only | Basic only | ✅ | ✅ |
| Credit rollover | — | credits never expire | 1 cycle | 1 cycle | 1 cycle |
| Priority support | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## 2. App Structure

Create a new Django app: `billing/`

```
billing/
├── __init__.py
├── apps.py
├── admin.py
├── models.py
├── services.py          # All Razorpay API calls — isolated here
├── webhooks.py          # Webhook event handlers (one function per event type)
├── decorators.py        # @require_feature, @require_credits
├── mixins.py            # PlanFeatureRequiredMixin for class-based views
├── context_processors.py # Inject billing state into all templates
├── signals.py           # post_save → auto-assign free tier on user creation
├── views.py             # Pricing, checkout, success, manage, modules
├── urls.py
├── management/
│   └── commands/
│       ├── setup_billing_plans.py    # Seeds CreditPack and SubscriptionPlan records
│       └── backfill_free_tier.py     # One-time: assigns free tier to existing users
├── migrations/
│   ├── 0001_initial.py
│   └── 0002_seed_plans.py            # Data migration for plans
└── templates/
    └── billing/
        ├── pricing.html
        ├── checkout_pack.html        # PAYG pack purchase
        ├── checkout_subscription.html
        ├── modules.html              # Module selection page
        ├── manage.html               # Subscription management + credit history
        ├── success.html
        ├── cancelled.html
        └── partials/
            ├── credit_balance.html   # Reusable credit widget for navbar
            ├── upgrade_nudge.html    # Contextual upgrade CTA component
            └── usage_bar.html       # X of Y credits used
```

Register in `core/settings.py` INSTALLED_APPS:

```python
"billing.apps.BillingConfig",
```

---

## 3. Database Models

### 3.1 CreditPack

Represents a purchasable one-time credit bundle.

```python
class CreditPack(models.Model):
    """
    Admin-managed credit bundles available for one-time purchase.
    Seeded via setup_billing_plans management command.
    """
    CURRENCY_INR = 'INR'
    CURRENCY_USD = 'USD'
    CURRENCY_CHOICES = [(CURRENCY_INR, 'INR'), (CURRENCY_USD, 'USD')]

    name = models.CharField(max_length=100)           # "Basic", "Standard", "Pro"
    credits = models.IntegerField()                   # credits granted on purchase
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['currency', 'display_order']
        unique_together = ('name', 'currency')

    def __str__(self):
        return f"{self.name} ({self.currency}) — {self.credits} credits"
```

### 3.2 SubscriptionPlan

Represents a recurring plan with monthly credit grants.

```python
class SubscriptionPlan(models.Model):
    """
    Admin-managed subscription tiers.
    razorpay_plan_id_* created on Razorpay dashboard and stored here.
    """
    TIER_STARTER = 'starter'
    TIER_GROWTH = 'growth'
    TIER_PRO = 'pro'
    TIER_CHOICES = [
        (TIER_STARTER, 'Starter'),
        (TIER_GROWTH, 'Growth'),
        (TIER_PRO, 'Pro'),
    ]

    BILLING_MONTHLY = 'monthly'
    BILLING_ANNUAL = 'annual'
    BILLING_CHOICES = [
        (BILLING_MONTHLY, 'Monthly'),
        (BILLING_ANNUAL, 'Annual'),
    ]

    CURRENCY_INR = 'INR'
    CURRENCY_USD = 'USD'
    CURRENCY_CHOICES = [(CURRENCY_INR, 'INR'), (CURRENCY_USD, 'USD')]

    tier = models.CharField(max_length=20, choices=TIER_CHOICES)
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CHOICES)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    credits_per_cycle = models.IntegerField()

    # Razorpay plan ID — created on Razorpay dashboard, stored here
    razorpay_plan_id = models.CharField(max_length=100, blank=True)

    # Feature flags for this plan tier
    one_time_scheduling = models.BooleanField(default=False)
    recurring_scheduling = models.BooleanField(default=False)
    max_smtp_configs = models.IntegerField(default=1)
    full_analytics = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)
    ai_generation_included = models.BooleanField(default=True)
    custom_fonts = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tier', 'billing_cycle', 'currency')
        ordering = ['currency', 'billing_cycle', 'price']

    def __str__(self):
        return f"{self.get_tier_display()} {self.get_billing_cycle_display()} ({self.currency})"
```

### 3.3 UserCredit

Running credit balance for each user. Single source of truth.

```python
class UserCredit(models.Model):
    """
    One record per user. The authoritative credit balance.
    All mutations go through CreditTransaction — never update balance directly
    in application code. Use CreditLedger.debit() and CreditLedger.credit().
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credits'
    )
    balance = models.IntegerField(default=0)
    lifetime_earned = models.IntegerField(default=0)   # audit: total ever credited
    lifetime_spent = models.IntegerField(default=0)    # audit: total ever debited
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} — {self.balance} credits"
```

### 3.4 CreditTransaction

Immutable audit log. Every credit movement recorded here.

```python
class CreditTransaction(models.Model):
    """
    Append-only audit log. Never update or delete rows.
    balance_after is a snapshot for auditability — do not rely on it for logic.
    """
    TYPE_SIGNUP_GRANT = 'signup_grant'
    TYPE_PACK_PURCHASE = 'pack_purchase'
    TYPE_SUBSCRIPTION_GRANT = 'subscription_grant'
    TYPE_DEBIT_EMAIL = 'debit_email'
    TYPE_DEBIT_DOCUMENT = 'debit_document'
    TYPE_DEBIT_AI = 'debit_ai'
    TYPE_ROLLOVER = 'rollover'
    TYPE_ADMIN_ADJUSTMENT = 'admin_adjustment'
    TYPE_REFUND = 'refund'

    TYPE_CHOICES = [
        (TYPE_SIGNUP_GRANT, 'Signup Grant'),
        (TYPE_PACK_PURCHASE, 'Pack Purchase'),
        (TYPE_SUBSCRIPTION_GRANT, 'Subscription Grant'),
        (TYPE_DEBIT_EMAIL, 'Email Send'),
        (TYPE_DEBIT_DOCUMENT, 'Document Generation'),
        (TYPE_DEBIT_AI, 'AI Generation'),
        (TYPE_ROLLOVER, 'Credit Rollover'),
        (TYPE_ADMIN_ADJUSTMENT, 'Admin Adjustment'),
        (TYPE_REFUND, 'Refund'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credit_transactions'
    )
    transaction_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    amount = models.IntegerField()           # positive = credit, negative = debit
    balance_after = models.IntegerField()   # snapshot at time of transaction
    reference_id = models.CharField(
        max_length=200, blank=True,
        help_text="razorpay_payment_id, campaign_id, or other reference."
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        direction = '+' if self.amount > 0 else ''
        return f"{self.user.email} {direction}{self.amount} ({self.get_transaction_type_display()})"
```

### 3.5 UserSubscription

Active subscription state for a user.

```python
class UserSubscription(models.Model):
    """
    Tracks a user's active subscription plan.
    A user with no subscription row is on the Free tier.
    Only one active subscription per user at a time (enforced in service layer).
    """
    STATUS_ACTIVE = 'active'
    STATUS_CANCELLED = 'cancelled'
    STATUS_PAST_DUE = 'past_due'
    STATUS_EXPIRED = 'expired'
    STATUS_PENDING = 'pending'   # Payment initiated but not confirmed yet
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_PAST_DUE, 'Past Due'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_PENDING, 'Pending'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscriptions'
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name='user_subscriptions'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    currency = models.CharField(max_length=3)
    razorpay_subscription_id = models.CharField(max_length=100, blank=True, db_index=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['razorpay_subscription_id']),
        ]

    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    def __str__(self):
        return f"{self.user.email} — {self.plan} ({self.status})"

    @classmethod
    def get_active_for_user(cls, user):
        """Returns the user's active subscription or None."""
        return cls.objects.filter(user=user, status=cls.STATUS_ACTIVE).first()
```

### 3.6 Payment

Records every payment event.

```python
class Payment(models.Model):
    """
    Records all successful and failed payment events.
    Created by webhook handler — never by UI directly.
    """
    TYPE_PACK = 'pack'
    TYPE_SUBSCRIPTION = 'subscription'
    TYPE_CHOICES = [
        (TYPE_PACK, 'Credit Pack'),
        (TYPE_SUBSCRIPTION, 'Subscription'),
    ]

    STATUS_CAPTURED = 'captured'
    STATUS_FAILED = 'failed'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (STATUS_CAPTURED, 'Captured'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_REFUNDED, 'Refunded'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    payment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    credit_pack = models.ForeignKey(
        CreditPack, on_delete=models.SET_NULL, null=True, blank=True
    )
    subscription = models.ForeignKey(
        UserSubscription, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payments'
    )
    razorpay_payment_id = models.CharField(max_length=100, unique=True)
    razorpay_order_id = models.CharField(max_length=100, blank=True)
    razorpay_subscription_id = models.CharField(max_length=100, blank=True)
    razorpay_signature = models.CharField(max_length=512, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    credits_granted = models.IntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.razorpay_payment_id} — {self.amount} {self.currency} ({self.status})"
```

### 3.7 WebhookEvent

Idempotency store for all incoming Razorpay webhooks.

```python
class WebhookEvent(models.Model):
    """
    Every incoming Razorpay webhook is stored here before processing.
    event_id unique constraint prevents double-processing.
    Always store, then process — never process without storing first.
    """
    event_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    processing_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processed', 'Processed'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped'),  # Unknown event types we chose to ignore
        ],
        default='pending'
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['processing_status']),
        ]

    def __str__(self):
        return f"{self.event_type} — {self.event_id} ({self.processing_status})"
```

### 3.8 UserModules

Tracks which product modules a user has activated.

```python
class UserModules(models.Model):
    """
    Controls which modules are visible and accessible for a user.
    This is a UI/UX filter, not a billing gate — credits are universal.
    Activated at onboarding; user can change in settings at any time.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='modules'
    )
    email_module = models.BooleanField(
        default=True,
        help_text="Access to email campaign builder and bulk email sending."
    )
    document_module = models.BooleanField(
        default=False,
        help_text="Access to document template builder and bulk generation."
    )
    onboarding_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        modules = []
        if self.email_module:
            modules.append('Email')
        if self.document_module:
            modules.append('Documents')
        return f"{self.user.email} — {', '.join(modules) or 'None'}"
```

---

## 4. Credit Economics

### CreditLedger Service

All credit balance mutations go through this single service. No view or task modifies `UserCredit.balance` directly.

**`billing/ledger.py`**

```python
import logging
from django.db import transaction
from django.utils import timezone
from .models import UserCredit, CreditTransaction

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Raised when a debit exceeds the user's current balance."""
    pass


class CreditLedger:
    """
    All credit mutations are atomic and append a CreditTransaction row.
    This is the ONLY place allowed to modify UserCredit.balance.
    """

    @staticmethod
    @transaction.atomic
    def credit(user, amount, transaction_type, reference_id='', note=''):
        """
        Add credits to a user's balance.
        Uses select_for_update to prevent race conditions.
        """
        if amount <= 0:
            raise ValueError("Credit amount must be positive.")

        user_credit = UserCredit.objects.select_for_update().get_or_create(
            user=user, defaults={'balance': 0}
        )[0]

        user_credit.balance += amount
        user_credit.lifetime_earned += amount
        user_credit.save(update_fields=['balance', 'lifetime_earned', 'updated_at'])

        CreditTransaction.objects.create(
            user=user,
            transaction_type=transaction_type,
            amount=amount,
            balance_after=user_credit.balance,
            reference_id=reference_id,
            note=note,
        )

        logger.info(
            f"[CREDIT] user={user.pk} +{amount} type={transaction_type} "
            f"balance_after={user_credit.balance} ref={reference_id}"
        )
        return user_credit.balance

    @staticmethod
    @transaction.atomic
    def debit(user, amount, transaction_type, reference_id='', note=''):
        """
        Deduct credits from a user's balance.
        Raises InsufficientCreditsError if balance < amount.
        Uses select_for_update to prevent overdraft in concurrent sends.
        """
        if amount <= 0:
            raise ValueError("Debit amount must be positive.")

        user_credit = UserCredit.objects.select_for_update().get_or_create(
            user=user, defaults={'balance': 0}
        )[0]

        if user_credit.balance < amount:
            raise InsufficientCreditsError(
                f"User {user.pk} has {user_credit.balance} credits, "
                f"needs {amount} for {transaction_type}."
            )

        user_credit.balance -= amount
        user_credit.lifetime_spent += amount
        user_credit.save(update_fields=['balance', 'lifetime_spent', 'updated_at'])

        CreditTransaction.objects.create(
            user=user,
            transaction_type=transaction_type,
            amount=-amount,
            balance_after=user_credit.balance,
            reference_id=reference_id,
            note=note,
        )

        logger.info(
            f"[DEBIT] user={user.pk} -{amount} type={transaction_type} "
            f"balance_after={user_credit.balance} ref={reference_id}"
        )
        return user_credit.balance

    @staticmethod
    def get_balance(user):
        """Returns current balance. Creates UserCredit if not exists."""
        obj, _ = UserCredit.objects.get_or_create(user=user, defaults={'balance': 0})
        return obj.balance
```

### Monthly Subscription Credit Rollover

When a subscription renews and new credits are granted, carry forward unused credits up to one cycle's worth.

```python
# In billing/webhooks.py — inside _handle_subscription_charged:

def _apply_subscription_renewal_credits(user, plan):
    """
    Grants monthly credits on subscription renewal.
    Rolls over unused credits — capped at 1x the plan's cycle amount.
    Excess credits above the cap are forfeited (prevents indefinite stockpiling).
    """
    current_balance = CreditLedger.get_balance(user)
    cap = plan.credits_per_cycle  # rollover cap = one cycle worth

    if current_balance > cap:
        # Forfeit excess credits silently — log it for visibility
        excess = current_balance - cap
        CreditLedger.debit(
            user=user,
            amount=excess,
            transaction_type=CreditTransaction.TYPE_ROLLOVER,
            note=f"Excess credits forfeited at renewal. Cap: {cap}."
        )

    # Grant new cycle credits
    CreditLedger.credit(
        user=user,
        amount=plan.credits_per_cycle,
        transaction_type=CreditTransaction.TYPE_SUBSCRIPTION_GRANT,
        note=f"Monthly grant for {plan.get_tier_display()} plan."
    )
```

---

## 5. Razorpay Integration

### 5.1 Payment Flows

**Flow A — PAYG Credit Pack (one-time):**
```
User selects pack
  → POST /billing/checkout/pack/<pack_id>/
  → services.create_razorpay_order(amount, currency)
  → Return order_id to frontend
  → Razorpay checkout popup (JS)
  → User pays
  → Razorpay calls /billing/webhook/
  → webhook handler verifies signature, grants credits
  → User sees updated balance
```

**Flow B — Monthly Subscription:**
```
User selects plan
  → POST /billing/checkout/subscription/<plan_id>/
  → services.create_razorpay_subscription(razorpay_plan_id)
  → Return subscription_id to frontend
  → Razorpay checkout popup (subscription mode)
  → User pays first cycle
  → Razorpay calls /billing/webhook/ (subscription.activated + payment.captured)
  → webhook handler activates UserSubscription, grants first cycle credits
  → Subsequent months: Razorpay calls webhook on each renewal
```

**Flow C — Annual Subscription (one-time order, not Razorpay recurring):**
```
Annual billing is treated as a PAYG pack purchase for 12x monthly credits
at a 25% discount. Simpler: no subscription management complexity.
User pays once, gets 12 months of credits upfront.
```

### 5.2 Razorpay Order Creation (for PAYG + Annual)

Razorpay amounts are in **paise (INR)** or **cents (USD)**.

```python
# billing/services.py

import razorpay
from django.conf import settings

def _get_client():
    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )

def create_order(amount_decimal, currency, receipt, notes=None):
    """
    Creates a Razorpay Order for a one-time payment.
    amount_decimal: Decimal or float (e.g., 399.00 for ₹399)
    currency: 'INR' or 'USD'
    receipt: Unique string (e.g., 'pack_1_user_42')
    Returns: Razorpay order dict with 'id' key
    """
    # Convert to smallest currency unit (paise / cents)
    amount_int = int(amount_decimal * 100)

    client = _get_client()
    order = client.order.create({
        'amount': amount_int,
        'currency': currency,
        'receipt': receipt,
        'notes': notes or {},
        'payment_capture': True,  # Auto-capture on payment
    })
    return order

def create_subscription(razorpay_plan_id, total_count=120, notes=None):
    """
    Creates a Razorpay Subscription for recurring billing.
    total_count=120 means max 120 billing cycles (10 years, effectively perpetual).
    Returns: Razorpay subscription dict with 'id' key
    """
    client = _get_client()
    subscription = client.subscription.create({
        'plan_id': razorpay_plan_id,
        'total_count': total_count,
        'quantity': 1,
        'notes': notes or {},
    })
    return subscription

def cancel_subscription(razorpay_subscription_id, cancel_at_cycle_end=True):
    """
    Cancels a Razorpay subscription.
    cancel_at_cycle_end=True: user keeps access until period_end.
    cancel_at_cycle_end=False: immediate cancellation (for refunds).
    """
    client = _get_client()
    client.subscription.cancel(
        razorpay_subscription_id,
        {'cancel_at_cycle_end': 1 if cancel_at_cycle_end else 0}
    )

def verify_payment_signature(payment_id, order_id, signature):
    """Verifies Razorpay payment signature for order-based payments."""
    client = _get_client()
    try:
        client.utility.verify_payment_signature({
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False

def verify_webhook_signature(payload_body: bytes, signature: str):
    """
    Verifies Razorpay webhook signature.
    payload_body must be raw bytes — do not decode before passing here.
    """
    client = _get_client()
    try:
        client.utility.verify_webhook_signature(
            payload_body.decode('utf-8'),
            signature,
            settings.RAZORPAY_WEBHOOK_SECRET
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        return False

def fetch_payment(payment_id):
    """Fetches payment details from Razorpay for verification."""
    client = _get_client()
    return client.payment.fetch(payment_id)

def fetch_subscription(subscription_id):
    """Fetches subscription state from Razorpay."""
    client = _get_client()
    return client.subscription.fetch(subscription_id)
```

---

## 6. Service Layer

Business logic lives here, not in views or webhooks.

**`billing/billing_service.py`**

```python
import logging
from django.db import transaction
from django.utils import timezone
from .models import (
    CreditPack, SubscriptionPlan, UserSubscription,
    Payment, UserCredit, CreditTransaction
)
from .ledger import CreditLedger
from . import services as razorpay_service

logger = logging.getLogger(__name__)


def initiate_pack_purchase(user, pack: CreditPack, currency: str):
    """
    Creates a Razorpay Order for a credit pack purchase.
    Returns the order dict for passing to the frontend checkout.
    Does NOT create any DB records yet — those happen in the webhook.
    """
    receipt = f"pack_{pack.pk}_user_{user.pk}_{int(timezone.now().timestamp())}"
    order = razorpay_service.create_order(
        amount_decimal=pack.price,
        currency=currency,
        receipt=receipt,
        notes={
            'pack_id': str(pack.pk),
            'user_id': str(user.pk),
            'credits': str(pack.credits),
        }
    )
    return order


def initiate_subscription(user, plan: SubscriptionPlan):
    """
    Creates a Razorpay Subscription and a pending UserSubscription record.
    Returns the Razorpay subscription dict for the frontend.
    """
    # Cancel any existing active subscription first
    existing = UserSubscription.get_active_for_user(user)
    if existing:
        raise ValueError("User already has an active subscription. Cancel it first.")

    rp_subscription = razorpay_service.create_subscription(
        razorpay_plan_id=plan.razorpay_plan_id,
        notes={'user_id': str(user.pk), 'plan_id': str(plan.pk)}
    )

    # Create pending record — activated by webhook
    UserSubscription.objects.create(
        user=user,
        plan=plan,
        status=UserSubscription.STATUS_PENDING,
        currency=plan.currency,
        razorpay_subscription_id=rp_subscription['id'],
    )

    return rp_subscription


def cancel_subscription_for_user(user, immediate=False):
    """
    Cancels the user's active subscription.
    immediate=False: user keeps access until period end (preferred UX).
    immediate=True: for refund scenarios.
    """
    sub = UserSubscription.get_active_for_user(user)
    if not sub:
        raise ValueError("No active subscription to cancel.")

    razorpay_service.cancel_subscription(
        sub.razorpay_subscription_id,
        cancel_at_cycle_end=not immediate
    )

    if immediate:
        sub.status = UserSubscription.STATUS_CANCELLED
        sub.cancelled_at = timezone.now()
    else:
        sub.cancel_at_period_end = True
    sub.save(update_fields=['status', 'cancelled_at', 'cancel_at_period_end'])


def get_user_billing_state(user):
    """
    Returns a dict summarising the user's complete billing state.
    Used by context_processors and views — single source of truth for UI.
    """
    balance = CreditLedger.get_balance(user)
    active_sub = UserSubscription.get_active_for_user(user)
    plan = active_sub.plan if active_sub else None

    # Determine feature flags
    if plan:
        features = {
            'one_time_scheduling': plan.one_time_scheduling,
            'recurring_scheduling': plan.recurring_scheduling,
            'max_smtp_configs': plan.max_smtp_configs,
            'full_analytics': plan.full_analytics,
            'priority_support': plan.priority_support,
            'ai_generation': plan.ai_generation_included,
            'custom_fonts': plan.custom_fonts,
            'recipient_limit': None,  # No limit for subscribers
        }
    else:
        # Free tier or PAYG
        has_ever_paid = Payment.objects.filter(user=user, status=Payment.STATUS_CAPTURED).exists()
        features = {
            'one_time_scheduling': False,
            'recurring_scheduling': False,
            'max_smtp_configs': 1,
            'full_analytics': False,
            'priority_support': False,
            'ai_generation': has_ever_paid,   # AI available to any paid user
            'custom_fonts': has_ever_paid,
            'recipient_limit': 50 if not has_ever_paid else None,
        }

    return {
        'credit_balance': balance,
        'subscription': active_sub,
        'plan': plan,
        'plan_tier': plan.tier if plan else 'free',
        'features': features,
        'cancel_at_period_end': active_sub.cancel_at_period_end if active_sub else False,
    }
```

---

## 7. Webhook Handler

**`billing/webhooks.py`**

The webhook view (in `views.py`) validates the signature and stores the `WebhookEvent` row, then delegates to a handler here. All handlers are idempotent.

```python
import logging
from django.utils import timezone
from .models import (
    WebhookEvent, UserSubscription, Payment,
    CreditPack, CreditTransaction
)
from .ledger import CreditLedger
from accounts.models import CustomUser

logger = logging.getLogger(__name__)


HANDLED_EVENTS = {
    'payment.captured',
    'payment.failed',
    'subscription.activated',
    'subscription.charged',
    'subscription.cancelled',
    'subscription.completed',
    'subscription.pending',
}


def dispatch(webhook_event: WebhookEvent):
    """
    Dispatches a stored WebhookEvent to the appropriate handler.
    Called after signature verification and storage.
    All handlers must be idempotent.
    """
    event_type = webhook_event.event_type
    payload = webhook_event.payload

    if event_type not in HANDLED_EVENTS:
        webhook_event.processing_status = 'skipped'
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processing_status', 'processed_at'])
        return

    try:
        handlers = {
            'payment.captured': _handle_payment_captured,
            'payment.failed': _handle_payment_failed,
            'subscription.activated': _handle_subscription_activated,
            'subscription.charged': _handle_subscription_charged,
            'subscription.cancelled': _handle_subscription_cancelled,
            'subscription.completed': _handle_subscription_cancelled,
        }
        handlers[event_type](payload)

        webhook_event.processing_status = 'processed'
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processing_status', 'processed_at'])

    except Exception as exc:
        logger.error(
            f"[WEBHOOK] Failed to process {event_type} "
            f"event_id={webhook_event.event_id}: {exc}",
            exc_info=True
        )
        webhook_event.processing_status = 'failed'
        webhook_event.failure_reason = str(exc)
        webhook_event.save(update_fields=['processing_status', 'failure_reason'])
        raise  # Re-raise so the view returns 500 — Razorpay will retry


def _handle_payment_captured(payload):
    """
    Handles payment.captured for BOTH pack purchases and first subscription payment.
    Idempotent: Payment.razorpay_payment_id has unique constraint.
    """
    payment_entity = payload['payload']['payment']['entity']
    razorpay_payment_id = payment_entity['id']
    razorpay_order_id = payment_entity.get('order_id', '')
    amount_paise = payment_entity['amount']
    currency = payment_entity['currency']
    notes = payment_entity.get('notes', {})

    # Skip if already processed (idempotency)
    if Payment.objects.filter(razorpay_payment_id=razorpay_payment_id).exists():
        logger.info(f"[WEBHOOK] payment.captured already processed: {razorpay_payment_id}")
        return

    # Resolve user
    user_id = notes.get('user_id')
    if not user_id:
        logger.error(f"[WEBHOOK] payment.captured missing user_id in notes: {razorpay_payment_id}")
        return
    user = CustomUser.objects.get(pk=user_id)

    # Resolve what was purchased (pack vs subscription first payment)
    pack_id = notes.get('pack_id')
    credits_granted = 0

    if pack_id:
        # PAYG pack purchase
        pack = CreditPack.objects.get(pk=pack_id)
        credits_granted = pack.credits

        Payment.objects.create(
            user=user,
            payment_type=Payment.TYPE_PACK,
            credit_pack=pack,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=razorpay_order_id,
            amount=amount_paise / 100,
            currency=currency,
            status=Payment.STATUS_CAPTURED,
            credits_granted=credits_granted,
            paid_at=timezone.now(),
        )
        CreditLedger.credit(
            user=user,
            amount=credits_granted,
            transaction_type=CreditTransaction.TYPE_PACK_PURCHASE,
            reference_id=razorpay_payment_id,
            note=f"Pack: {pack.name} ({credits_granted} credits)"
        )
        logger.info(
            f"[WEBHOOK] Pack purchase: user={user.pk} pack={pack.name} "
            f"credits={credits_granted} payment={razorpay_payment_id}"
        )


def _handle_payment_failed(payload):
    """Records failed payment for audit visibility."""
    payment_entity = payload['payload']['payment']['entity']
    razorpay_payment_id = payment_entity['id']
    notes = payment_entity.get('notes', {})
    user_id = notes.get('user_id')

    if not user_id:
        return

    user = CustomUser.objects.get(pk=user_id)
    Payment.objects.get_or_create(
        razorpay_payment_id=razorpay_payment_id,
        defaults={
            'user': user,
            'payment_type': Payment.TYPE_PACK,
            'amount': payment_entity['amount'] / 100,
            'currency': payment_entity['currency'],
            'status': Payment.STATUS_FAILED,
            'credits_granted': 0,
        }
    )


def _handle_subscription_activated(payload):
    """
    Called when Razorpay confirms the subscription is active.
    Updates UserSubscription status from pending → active.
    Credits are granted in _handle_subscription_charged (on first payment).
    """
    sub_entity = payload['payload']['subscription']['entity']
    razorpay_sub_id = sub_entity['id']

    try:
        user_sub = UserSubscription.objects.get(razorpay_subscription_id=razorpay_sub_id)
    except UserSubscription.DoesNotExist:
        logger.error(f"[WEBHOOK] subscription.activated: no UserSubscription for {razorpay_sub_id}")
        return

    if user_sub.status != UserSubscription.STATUS_ACTIVE:
        user_sub.status = UserSubscription.STATUS_ACTIVE
        user_sub.save(update_fields=['status', 'updated_at'])
        logger.info(f"[WEBHOOK] Subscription activated: {razorpay_sub_id}")


def _handle_subscription_charged(payload):
    """
    Called on every successful billing cycle (including the first).
    Grants monthly credits to the user.
    """
    payment_entity = payload['payload']['payment']['entity']
    sub_entity = payload['payload']['subscription']['entity']
    razorpay_payment_id = payment_entity['id']
    razorpay_sub_id = sub_entity['id']

    # Idempotency check
    if Payment.objects.filter(razorpay_payment_id=razorpay_payment_id).exists():
        logger.info(f"[WEBHOOK] subscription.charged already processed: {razorpay_payment_id}")
        return

    try:
        user_sub = UserSubscription.objects.select_related('user', 'plan').get(
            razorpay_subscription_id=razorpay_sub_id
        )
    except UserSubscription.DoesNotExist:
        logger.error(f"[WEBHOOK] subscription.charged: no UserSubscription for {razorpay_sub_id}")
        return

    user = user_sub.user
    plan = user_sub.plan
    credits_to_grant = plan.credits_per_cycle

    # Apply rollover cap (carries forward up to 1 cycle, forfeits excess)
    _apply_subscription_renewal_credits(user, plan)

    Payment.objects.create(
        user=user,
        payment_type=Payment.TYPE_SUBSCRIPTION,
        subscription=user_sub,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_subscription_id=razorpay_sub_id,
        amount=payment_entity['amount'] / 100,
        currency=payment_entity['currency'],
        status=Payment.STATUS_CAPTURED,
        credits_granted=credits_to_grant,
        paid_at=timezone.now(),
    )

    # Update period dates
    user_sub.current_period_start = timezone.now()
    user_sub.status = UserSubscription.STATUS_ACTIVE
    user_sub.save(update_fields=['current_period_start', 'status', 'updated_at'])

    logger.info(
        f"[WEBHOOK] Subscription charged: user={user.pk} plan={plan} "
        f"credits={credits_to_grant} payment={razorpay_payment_id}"
    )


def _apply_subscription_renewal_credits(user, plan):
    """Rollover cap logic. See Section 4 for explanation."""
    from .ledger import CreditLedger, InsufficientCreditsError
    current_balance = CreditLedger.get_balance(user)
    cap = plan.credits_per_cycle

    if current_balance > cap:
        excess = current_balance - cap
        CreditLedger.debit(
            user=user, amount=excess,
            transaction_type=CreditTransaction.TYPE_ROLLOVER,
            note=f"Excess credits forfeited at renewal. Cap: {cap}."
        )

    CreditLedger.credit(
        user=user, amount=plan.credits_per_cycle,
        transaction_type=CreditTransaction.TYPE_SUBSCRIPTION_GRANT,
        note=f"Monthly grant: {plan.get_tier_display()} plan."
    )


def _handle_subscription_cancelled(payload):
    """Marks UserSubscription as cancelled."""
    sub_entity = payload['payload']['subscription']['entity']
    razorpay_sub_id = sub_entity['id']

    try:
        user_sub = UserSubscription.objects.get(razorpay_subscription_id=razorpay_sub_id)
    except UserSubscription.DoesNotExist:
        return

    user_sub.status = UserSubscription.STATUS_CANCELLED
    user_sub.cancelled_at = timezone.now()
    user_sub.save(update_fields=['status', 'cancelled_at', 'updated_at'])
    logger.info(f"[WEBHOOK] Subscription cancelled: {razorpay_sub_id}")
```

### Webhook View (in `views.py`)

```python
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponse
import json

@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Receives all Razorpay webhook events.
    1. Verify signature immediately.
    2. Store WebhookEvent (idempotency).
    3. Dispatch to handler.
    Always return 200 to Razorpay after storing — even if processing fails,
    so Razorpay doesn't retry endlessly for non-transient errors.
    Return 500 only for transient errors (handler should re-raise those).
    """
    from .models import WebhookEvent
    from . import webhooks
    from .services import verify_webhook_signature

    signature = request.headers.get('X-Razorpay-Signature', '')
    if not verify_webhook_signature(request.body, signature):
        return HttpResponse(status=400)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_id = payload.get('id', '')
    event_type = payload.get('event', '')

    # Store first — idempotency guaranteed by unique constraint on event_id
    event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={'event_type': event_type, 'payload': payload}
    )

    if not created:
        # Already seen this event — skip processing
        return HttpResponse(status=200)

    try:
        webhooks.dispatch(event)
    except Exception:
        # Handler already logged the error and saved 'failed' status
        # Return 500 so Razorpay retries (transient failure path)
        return HttpResponse(status=500)

    return HttpResponse(status=200)
```

---

## 8. Feature Gating

### Decorators (for function-based views)

**`billing/decorators.py`**

```python
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from .billing_service import get_user_billing_state


def require_feature(feature_key, upgrade_message=None):
    """
    Blocks access if the user's plan doesn't have the named feature.
    Usage: @require_feature('recurring_scheduling')

    feature_key must match a key in get_user_billing_state()['features'].
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            state = get_user_billing_state(request.user)
            if state['features'].get(feature_key):
                return view_func(request, *args, **kwargs)
            msg = upgrade_message or "This feature requires a higher plan."
            messages.warning(request, msg)
            return redirect('billing:pricing')
        return wrapper
    return decorator


def require_credits(minimum=1):
    """
    Blocks access if the user has fewer than minimum credits.
    Usage: @require_credits(minimum=2)
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from .ledger import CreditLedger
            balance = CreditLedger.get_balance(request.user)
            if balance >= minimum:
                return view_func(request, *args, **kwargs)
            messages.warning(
                request,
                f"You need at least {minimum} credits. "
                f"Your balance: {balance}. Top up to continue."
            )
            return redirect('billing:pricing')
        return wrapper
    return decorator
```

### Mixins (for class-based views)

**`billing/mixins.py`**

```python
from django.shortcuts import redirect
from django.contrib import messages
from .billing_service import get_user_billing_state


class FeatureRequiredMixin:
    """
    Add to any CBV. Set required_feature = 'feature_key' on the subclass.
    """
    required_feature = None
    upgrade_message = "This feature requires a higher plan."

    def dispatch(self, request, *args, **kwargs):
        if not self.required_feature:
            raise ValueError("FeatureRequiredMixin requires 'required_feature' to be set.")
        state = get_user_billing_state(request.user)
        if state['features'].get(self.required_feature):
            return super().dispatch(request, *args, **kwargs)
        messages.warning(request, self.upgrade_message)
        return redirect('billing:pricing')


class CreditRequiredMixin:
    """
    Add to any CBV that requires a minimum credit balance before proceeding.
    Set minimum_credits = N on the subclass.
    """
    minimum_credits = 1

    def dispatch(self, request, *args, **kwargs):
        from .ledger import CreditLedger
        balance = CreditLedger.get_balance(request.user)
        if balance >= self.minimum_credits:
            return super().dispatch(request, *args, **kwargs)
        messages.warning(request, f"Insufficient credits. You need {self.minimum_credits}, have {balance}.")
        return redirect('billing:pricing')
```

### Usage Examples

```python
# In emails/views.py
@require_feature('one_time_scheduling', upgrade_message="Scheduling requires a Starter plan or above.")
def schedule_campaign(request, pk):
    ...

# In documents/views.py
class CreateDocumentCampaignView(CreditRequiredMixin, LoginRequiredMixin, CreateView):
    minimum_credits = 2
    ...

# In emails/views.py — recurring schedule
class CreateRecurringScheduleView(FeatureRequiredMixin, LoginRequiredMixin, CreateView):
    required_feature = 'recurring_scheduling'
    upgrade_message = "Recurring schedules require a Growth plan."
    ...
```

---

## 9. Module Selection

### Onboarding Flow

After signup and email verification, redirect user to `/billing/modules/` if `UserModules.onboarding_completed = False`.

```python
# billing/views.py

def module_selection(request):
    """
    Onboarding step: user selects which modules they want.
    Can also be accessed later from Settings to change selection.
    """
    modules, _ = UserModules.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        email_module = 'email_module' in request.POST
        document_module = 'document_module' in request.POST

        if not email_module and not document_module:
            messages.error(request, "Please select at least one module.")
            return render(request, 'billing/modules.html', {'modules': modules})

        modules.email_module = email_module
        modules.document_module = document_module
        modules.onboarding_completed = True
        modules.save()

        messages.success(request, "Your workspace is ready.")
        return redirect('dashboard')

    return render(request, 'billing/modules.html', {'modules': modules})
```

### Module Enforcement Middleware

```python
# billing/middleware.py

class ModuleOnboardingMiddleware:
    """
    Redirects authenticated users who haven't completed module selection
    to the onboarding page. Exempts billing and auth URLs.
    """
    EXEMPT_PATHS = {'/billing/modules/', '/accounts/', '/billing/webhook/'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and not request.user.is_staff
            and not any(request.path.startswith(p) for p in self.EXEMPT_PATHS)
        ):
            modules = getattr(request.user, 'modules', None)
            if modules is None or not modules.onboarding_completed:
                from django.shortcuts import redirect
                return redirect('billing:modules')
        return self.get_response(request)
```

Register in `core/settings.py` MIDDLEWARE (after `AuthenticationMiddleware`):

```python
"billing.middleware.ModuleOnboardingMiddleware",
```

### Module Context in Templates

**`billing/context_processors.py`**

```python
def billing_state(request):
    """
    Injects billing state into all templates.
    Used by navbar credit widget, upgrade nudges, feature-gated UI elements.
    """
    if not request.user.is_authenticated:
        return {}

    from .billing_service import get_user_billing_state
    from .models import UserModules

    state = get_user_billing_state(request.user)
    modules, _ = UserModules.objects.get_or_create(user=request.user)

    return {
        'billing': state,
        'user_modules': modules,
        'credit_balance': state['credit_balance'],
        'plan_tier': state['plan_tier'],
        'billing_features': state['features'],
    }
```

Register in `core/settings.py`:

```python
"billing.context_processors.billing_state",
```

---

## 10. Upgrade Nudge System

Nudges appear contextually — never interrupt mid-task, always at natural breakpoints.

### Nudge Triggers and Messages

```python
# billing/nudges.py

from .ledger import CreditLedger
from .models import Payment


def get_nudge_for_user(user, context=None):
    """
    Returns a nudge dict or None.
    context: optional string indicating where the nudge will be shown.
             Used to select the most relevant message.
    Returns: {'message': str, 'cta': str, 'cta_url': str} or None
    """
    from .billing_service import get_user_billing_state
    state = get_user_billing_state(user)
    balance = state['credit_balance']
    plan_tier = state['plan_tier']

    # Nudge: low credits
    if 0 < balance <= 60:
        return {
            'type': 'low_credits',
            'message': f"You have {balance} credits left — enough for roughly {balance // 2} document deliveries.",
            'cta': 'Top up credits',
            'cta_url': '/billing/pricing/#packs',
        }

    # Nudge: third pack purchase — push toward subscription
    pack_purchases = Payment.objects.filter(
        user=user, payment_type=Payment.TYPE_PACK, status=Payment.STATUS_CAPTURED
    ).count()
    if pack_purchases == 3 and plan_tier == 'free':
        return {
            'type': 'subscription_push',
            'message': "You've bought 3 credit packs. A Growth plan gives 5,000 credits/month for less.",
            'cta': 'View plans',
            'cta_url': '/billing/pricing/#plans',
        }

    # Nudge: tried to schedule (context-specific)
    if context == 'schedule_blocked' and plan_tier == 'free':
        return {
            'type': 'feature_gate',
            'message': "Scheduling campaigns requires a Starter plan — from ₹349/month.",
            'cta': 'Upgrade now',
            'cta_url': '/billing/pricing/#plans',
        }

    # Nudge: tried to add second SMTP
    if context == 'smtp_limit' and plan_tier in ('free', 'starter'):
        return {
            'type': 'feature_gate',
            'message': "Multiple SMTP configurations are available on Growth plan.",
            'cta': 'See Growth plan',
            'cta_url': '/billing/pricing/#plans',
        }

    return None
```

### Template Usage

```html
<!-- In templates — any page where a nudge is relevant -->
{% load billing_tags %}
{% get_nudge request.user as nudge %}
{% if nudge %}
  {% include 'billing/partials/upgrade_nudge.html' with nudge=nudge %}
{% endif %}
```

---

## 11. Views and URL Structure

### URL Patterns

**`billing/urls.py`**

```python
urlpatterns = [
    path('pricing/', views.pricing, name='pricing'),
    path('modules/', views.module_selection, name='modules'),
    path('checkout/pack/<int:pack_id>/', views.checkout_pack, name='checkout_pack'),
    path('checkout/subscription/<int:plan_id>/', views.checkout_subscription, name='checkout_subscription'),
    path('success/', views.payment_success, name='success'),
    path('manage/', views.manage_subscription, name='manage'),
    path('cancel/', views.cancel_subscription, name='cancel'),
    path('history/', views.credit_history, name='history'),
    path('webhook/', views.razorpay_webhook, name='webhook'),    # CSRF-exempt
]
```

Include in `core/urls.py`:

```python
path('billing/', include('billing.urls', namespace='billing')),
```

### Key Views Summary

| URL | View | Notes |
|---|---|---|
| `/billing/pricing/` | Geo-aware: shows INR or USD packs + plans | Uses `core/pricing.py` geo-detection |
| `/billing/modules/` | Module selection | Redirect target after signup |
| `/billing/checkout/pack/<id>/` | Creates Razorpay Order, renders Razorpay JS checkout | POST only |
| `/billing/checkout/subscription/<id>/` | Creates Razorpay Subscription | POST only |
| `/billing/success/` | Post-payment success page | Reads payment result from session |
| `/billing/manage/` | Current plan, credits, cancel button, payment history | |
| `/billing/history/` | Full `CreditTransaction` log | |
| `/billing/webhook/` | Razorpay webhook endpoint | CSRF-exempt, POST only |

---

## 12. Credit Deduction in Send Pipeline

Credit deduction happens **per recipient** inside the Celery send task, not at campaign start. This ensures users are only charged for work that was actually attempted.

**Modify `emails/tasks.py`:**

```python
# At the top of the per-recipient send block inside send_campaign_emails:

from billing.ledger import CreditLedger, InsufficientCreditsError
from billing.models import CreditTransaction

def _process_single_recipient(recipient, campaign, smtp_connection):
    """
    Processes one recipient. Deducts credits before attempting send.
    If credits are insufficient, marks recipient as failed with a clear message
    and transitions the campaign to 'failed'.
    """
    # Determine credit cost
    if campaign.is_document_campaign:
        credit_cost = 2    # document generation + email delivery
        transaction_type = CreditTransaction.TYPE_DEBIT_DOCUMENT
    else:
        credit_cost = 1    # email send only
        transaction_type = CreditTransaction.TYPE_DEBIT_EMAIL

    try:
        CreditLedger.debit(
            user=campaign.user,
            amount=credit_cost,
            transaction_type=transaction_type,
            reference_id=str(campaign.pk),
            note=f"Campaign: {campaign.name} | Recipient: {recipient.email}"
        )
    except InsufficientCreditsError:
        recipient.status = 'failed'
        recipient.error_message = (
            "Insufficient credits. Campaign paused. "
            "Top up your credits to resume."
        )
        recipient.save(update_fields=['status', 'error_message', 'updated_at'])

        # Transition campaign to failed
        campaign.status = 'failed'
        campaign.save(update_fields=['status'])

        logger.warning(
            f"[SEND] Campaign {campaign.pk} paused — user {campaign.user.pk} "
            f"ran out of credits at recipient {recipient.pk}."
        )
        return False   # Signal to outer loop to stop processing

    # ... proceed with actual email/document send logic
    return True
```

**Credit deduction for AI generation** (in `emails/views.py` when AI template is called):

```python
from billing.ledger import CreditLedger, InsufficientCreditsError
from billing.models import CreditTransaction

def generate_ai_template(request):
    try:
        CreditLedger.debit(
            user=request.user,
            amount=5,
            transaction_type=CreditTransaction.TYPE_DEBIT_AI,
            reference_id='ai_generation',
        )
    except InsufficientCreditsError:
        return JsonResponse({'error': 'Insufficient credits for AI generation.'}, status=402)

    # ... call Gemini API and return result
```

---

## 13. Admin Panel

**`billing/admin.py`**

```python
from django.contrib import admin
from .models import (
    CreditPack, SubscriptionPlan, UserCredit,
    CreditTransaction, UserSubscription, Payment, WebhookEvent, UserModules
)


@admin.register(CreditPack)
class CreditPackAdmin(admin.ModelAdmin):
    list_display = ['name', 'currency', 'credits', 'price', 'is_active', 'display_order']
    list_filter = ['currency', 'is_active']
    ordering = ['currency', 'display_order']


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['tier', 'billing_cycle', 'currency', 'price', 'credits_per_cycle', 'is_active']
    list_filter = ['tier', 'billing_cycle', 'currency', 'is_active']


@admin.register(UserCredit)
class UserCreditAdmin(admin.ModelAdmin):
    list_display = ['user', 'balance', 'lifetime_earned', 'lifetime_spent', 'updated_at']
    search_fields = ['user__email']
    readonly_fields = ['lifetime_earned', 'lifetime_spent', 'updated_at']


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'transaction_type', 'amount', 'balance_after', 'reference_id', 'created_at']
    list_filter = ['transaction_type']
    search_fields = ['user__email', 'reference_id']
    readonly_fields = ['user', 'transaction_type', 'amount', 'balance_after', 'reference_id', 'note', 'created_at']
    ordering = ['-created_at']

    def has_add_permission(self, request):
        return False   # Never create transactions manually

    def has_change_permission(self, request, obj=None):
        return False   # Immutable audit log

    def has_delete_permission(self, request, obj=None):
        return False   # Never delete


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'plan', 'status', 'currency', 'current_period_end', 'cancel_at_period_end']
    list_filter = ['status', 'currency']
    search_fields = ['user__email', 'razorpay_subscription_id']
    readonly_fields = ['razorpay_subscription_id', 'created_at', 'updated_at']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['user', 'payment_type', 'amount', 'currency', 'status', 'credits_granted', 'paid_at']
    list_filter = ['payment_type', 'status', 'currency']
    search_fields = ['user__email', 'razorpay_payment_id']
    readonly_fields = [f.name for f in Payment._meta.fields]


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ['event_type', 'event_id', 'processing_status', 'created_at', 'processed_at']
    list_filter = ['processing_status', 'event_type']
    search_fields = ['event_id']
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]


@admin.register(UserModules)
class UserModulesAdmin(admin.ModelAdmin):
    list_display = ['user', 'email_module', 'document_module', 'onboarding_completed']
    list_filter = ['email_module', 'document_module', 'onboarding_completed']
    search_fields = ['user__email']
```

---

## 14. Settings and Environment

**`.env` additions:**

```ini
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxxxxxxxxxxx
SIGNUP_FREE_CREDITS=100
```

**`core/settings.py` additions:**

```python
# Razorpay
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

# Billing
SIGNUP_FREE_CREDITS = int(os.getenv("SIGNUP_FREE_CREDITS", 100))

# AI credit cost
AI_GENERATION_CREDIT_COST = 5

# Credit costs per action
CREDIT_COST_EMAIL = 1
CREDIT_COST_DOCUMENT_ONLY = 1
CREDIT_COST_DOCUMENT_WITH_EMAIL = 2
```

---

## 15. Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    # ... existing ...
    "razorpay>=1.4.2",
]
```

No additional billing-specific dependencies needed. Razorpay's Python SDK handles everything.

---

## 16. Data Seeding

**`billing/management/commands/setup_billing_plans.py`**

This command must be run once after the first migration. It creates all `CreditPack` and `SubscriptionPlan` records.

**Important:** After creating `SubscriptionPlan` records, you must manually create corresponding plans on the Razorpay Dashboard and paste the Razorpay plan IDs back into each `SubscriptionPlan.razorpay_plan_id`. This is a one-time setup step.

```python
class Command(BaseCommand):
    help = "Seeds CreditPack and SubscriptionPlan records."

    def handle(self, *args, **kwargs):
        self._seed_credit_packs()
        self._seed_subscription_plans()
        self.stdout.write(self.style.SUCCESS(
            "Billing plans seeded. "
            "IMPORTANT: Create matching plans on Razorpay Dashboard "
            "and update razorpay_plan_id on each SubscriptionPlan record."
        ))

    def _seed_credit_packs(self):
        inr_packs = [
            {'name': 'Trial', 'credits': 300, 'price': 149, 'display_order': 1},
            {'name': 'Basic', 'credits': 1000, 'price': 399, 'display_order': 2},
            {'name': 'Standard', 'credits': 3000, 'price': 999, 'display_order': 3},
            {'name': 'Pro', 'credits': 8000, 'price': 2199, 'display_order': 4},
            {'name': 'Business', 'credits': 20000, 'price': 4499, 'display_order': 5},
        ]
        usd_packs = [
            {'name': 'Trial', 'credits': 300, 'price': '3.99', 'display_order': 1},
            {'name': 'Basic', 'credits': 1000, 'price': '9.99', 'display_order': 2},
            {'name': 'Standard', 'credits': 3000, 'price': '24.99', 'display_order': 3},
            {'name': 'Pro', 'credits': 8000, 'price': '54.99', 'display_order': 4},
            {'name': 'Business', 'credits': 20000, 'price': '109.99', 'display_order': 5},
        ]
        for pack in inr_packs:
            CreditPack.objects.update_or_create(
                name=pack['name'], currency='INR',
                defaults={**pack, 'currency': 'INR'}
            )
        for pack in usd_packs:
            CreditPack.objects.update_or_create(
                name=pack['name'], currency='USD',
                defaults={**pack, 'currency': 'USD'}
            )

    def _seed_subscription_plans(self):
        plans = [
            # INR monthly
            {'tier': 'starter', 'billing_cycle': 'monthly', 'currency': 'INR', 'price': 349, 'credits_per_cycle': 1500, 'one_time_scheduling': True},
            {'tier': 'growth', 'billing_cycle': 'monthly', 'currency': 'INR', 'price': 899, 'credits_per_cycle': 5000, 'one_time_scheduling': True, 'recurring_scheduling': True, 'max_smtp_configs': 3, 'full_analytics': True},
            {'tier': 'pro', 'billing_cycle': 'monthly', 'currency': 'INR', 'price': 2199, 'credits_per_cycle': 15000, 'one_time_scheduling': True, 'recurring_scheduling': True, 'max_smtp_configs': 5, 'full_analytics': True, 'priority_support': True},
            # USD monthly
            {'tier': 'starter', 'billing_cycle': 'monthly', 'currency': 'USD', 'price': 9, 'credits_per_cycle': 1500, 'one_time_scheduling': True},
            {'tier': 'growth', 'billing_cycle': 'monthly', 'currency': 'USD', 'price': 22, 'credits_per_cycle': 5000, 'one_time_scheduling': True, 'recurring_scheduling': True, 'max_smtp_configs': 3, 'full_analytics': True},
            {'tier': 'pro', 'billing_cycle': 'monthly', 'currency': 'USD', 'price': 55, 'credits_per_cycle': 15000, 'one_time_scheduling': True, 'recurring_scheduling': True, 'max_smtp_configs': 5, 'full_analytics': True, 'priority_support': True},
        ]
        for plan_data in plans:
            SubscriptionPlan.objects.update_or_create(
                tier=plan_data['tier'],
                billing_cycle=plan_data['billing_cycle'],
                currency=plan_data['currency'],
                defaults=plan_data
            )
```

**Auto-assign signup credits via signal:**

**`billing/signals.py`**

```python
from django.db.models.signals import post_save
from django.conf import settings
from .models import UserCredit, UserModules, CreditTransaction
from .ledger import CreditLedger

def on_user_created(sender, instance, created, **kwargs):
    if not created:
        return
    # Create credit wallet
    UserCredit.objects.get_or_create(user=instance, defaults={'balance': 0})
    # Grant signup credits
    free_credits = getattr(settings, 'SIGNUP_FREE_CREDITS', 100)
    if free_credits > 0:
        CreditLedger.credit(
            user=instance,
            amount=free_credits,
            transaction_type=CreditTransaction.TYPE_SIGNUP_GRANT,
            note="Welcome credits on signup."
        )
    # Create module record (onboarding not complete yet)
    UserModules.objects.get_or_create(user=instance)

# Connect in billing/apps.py:
# def ready(self): from . import signals; post_save.connect(signals.on_user_created, sender=CustomUser)
```

---

## 17. Migration Strategy

Run in this order:

```bash
# 1. Create billing app migrations
uv run python manage.py makemigrations billing

# 2. Apply migrations
uv run python manage.py migrate

# 3. Seed plans
uv run python manage.py setup_billing_plans

# 4. Backfill existing users with free credits and module records
uv run python manage.py backfill_free_tier

# 5. Manually create Razorpay plans on dashboard
#    → paste plan IDs into SubscriptionPlan records via admin

# 6. Set RAZORPAY_WEBHOOK_SECRET in .env
#    → Configure webhook URL in Razorpay dashboard: https://yourdomain.io/billing/webhook/
#    → Subscribe to: payment.captured, payment.failed, subscription.activated,
#      subscription.charged, subscription.cancelled, subscription.completed
```

---

## 18. Testing Plan

### Unit Tests (`billing/tests/`)

```
test_ledger.py
  - test_credit_debit_basic
  - test_debit_raises_insufficient_credits
  - test_concurrent_debit_no_overdraft  (ThreadPoolExecutor with 10 concurrent debits)
  - test_credit_creates_transaction_row
  - test_debit_creates_negative_transaction_row

test_webhooks.py
  - test_payment_captured_grants_credits
  - test_payment_captured_idempotent (duplicate event_id → credits granted once)
  - test_subscription_charged_applies_rollover_cap
  - test_subscription_cancelled_updates_status
  - test_invalid_signature_returns_400

test_billing_service.py
  - test_get_user_billing_state_free_user
  - test_get_user_billing_state_subscriber
  - test_cancel_subscription_sets_cancel_at_period_end

test_feature_gating.py
  - test_require_feature_blocks_free_user
  - test_require_feature_passes_subscriber
  - test_require_credits_blocks_zero_balance
  - test_require_credits_passes_with_sufficient_balance

test_module_middleware.py
  - test_redirects_unonboarded_user
  - test_exempts_billing_urls
  - test_passes_onboarded_user
```

### Integration Tests

```
test_pack_purchase_flow.py
  - Simulate full Razorpay webhook for payment.captured → assert credits added

test_subscription_lifecycle.py
  - Create subscription → activate → charge → cancel → verify state at each step
```

### Manual Razorpay Test Checklist

Using Razorpay test keys and test card numbers:

- [ ] PAYG pack purchase → credits appear in balance
- [ ] Subscription creation → pending UserSubscription created
- [ ] Subscription activation webhook → status → active
- [ ] Subscription charged webhook → credits granted
- [ ] Rollover cap: start with 800 credits, grant 1500 → expect 1500 (capped, not 2300)
- [ ] Cancel subscription → cancel_at_period_end = True
- [ ] Failed payment webhook → Payment record with failed status
- [ ] Duplicate webhook event_id → processed only once

---

## 19. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Razorpay webhook duplicate delivery | High | `WebhookEvent.event_id` unique constraint. Idempotency check before every handler. |
| Credits overdraft under concurrent Celery tasks | High | `select_for_update()` in `CreditLedger.debit()`. Atomic transactions. |
| Razorpay plan ID not set before users try to subscribe | Medium | Guard in `checkout_subscription` view: check `plan.razorpay_plan_id != ''` before proceeding. |
| User runs out of credits mid-campaign | Medium | Per-recipient debit; campaign transitions to `failed` with clear message on InsufficientCreditsError. |
| Module onboarding redirect loops | Low | Exempt `/billing/`, `/accounts/`, `/admin/` from `ModuleOnboardingMiddleware`. |
| SIGNUP_FREE_CREDITS granted twice (signal fires twice) | Low | `get_or_create(UserCredit)` on credit wallet; check `lifetime_earned == 0` before granting. |
| Razorpay International USD support requires business verification | Medium | Test with INR first. International USD is a separate Razorpay product — initiate verification process early. |
| CreditTransaction row deleted accidentally | Low | `has_delete_permission = False` in admin. Transaction rows are immutable by design. |

---

## 20. Execution Order

```
Step 1: Create billing/ app skeleton and register in INSTALLED_APPS
Step 2: Write all models (billing/models.py)
Step 3: Write CreditLedger (billing/ledger.py)
Step 4: Write billing/signals.py + connect in apps.py
Step 5: makemigrations + migrate
Step 6: Write setup_billing_plans + backfill_free_tier management commands
Step 7: Run seed commands
Step 8: Write services.py (Razorpay API calls)
Step 9: Write webhooks.py (event handlers)
Step 10: Write webhook view + URL (test with Razorpay test webhook tool)
Step 11: Write billing_service.py (get_user_billing_state)
Step 12: Write decorators.py + mixins.py
Step 13: Write context_processors.py + register
Step 14: Write ModuleOnboardingMiddleware + register
Step 15: Wire feature gating into emails/views.py and documents/views.py
Step 16: Wire credit deduction into emails/tasks.py and documents/tasks.py
Step 17: Write pricing view + template
Step 18: Write module selection view + template
Step 19: Write manage view + history view + templates
Step 20: Write checkout views (pack + subscription) with Razorpay JS integration
Step 21: Write nudges.py + template tag
Step 22: Write admin.py
Step 23: Write tests
Step 24: Manual Razorpay test checklist

Estimated effort: 5–7 days
```

---

*Plan authored: 2026-06-07. Execute in order. Do not skip Step 6 (seed) before Step 10 (webhooks tested).*
