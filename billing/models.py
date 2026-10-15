"""Billing data model (full plan schema).

Money/credit records are plain ``models.Model`` (never ``SoftDeleteModel``) and
are excluded from the soft-delete purge: ledgers, payments and receipts are
immutable financial history (a deliberate exception to the project-wide
soft-delete invariant). See docs/billing_accounting_profile_plan.md.
"""
import uuid

from django.conf import settings
from django.db import models, transaction


class BillingProfile(models.Model):
    """Billing address and optional tax identity. Snapshotted onto each receipt
    at purchase time so later edits never rewrite historical documents."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='billing_profile')
    billing_name = models.CharField(max_length=200, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True)
    tax_id = models.CharField(max_length=30, blank=True)  # GSTIN later; blank now
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"BillingProfile({self.user_id})"

    def address_block(self):
        parts = [self.address_line1, self.address_line2,
                 ', '.join(p for p in [self.city, self.state, self.postal_code] if p),
                 self.country]
        return '\n'.join(p for p in parts if p)


class CreditWallet(models.Model):
    """Authoritative credit balance, one row per user. Mutated only via
    ``billing.wallet`` so the ledger and balance never diverge."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='wallet')
    balance = models.IntegerField(default=0)
    lifetime_earned = models.IntegerField(default=0)
    lifetime_spent = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet({self.user_id}): {self.balance}"


class CreditTransaction(models.Model):
    """Append-only ledger. One immutable row per credit movement."""

    TYPE_SIGNUP_GRANT = 'signup_grant'
    TYPE_PACK_PURCHASE = 'pack_purchase'
    TYPE_PLAN_GRANT = 'plan_grant'
    TYPE_DEBIT_DOCUMENT = 'debit_document'
    TYPE_DEBIT_AI = 'debit_ai'
    TYPE_ROLLOVER = 'rollover'
    TYPE_REFUND = 'refund'
    TYPE_ADMIN_ADJUSTMENT = 'admin_adjustment'
    TYPE_CHOICES = [
        (TYPE_SIGNUP_GRANT, 'Signup grant'),
        (TYPE_PACK_PURCHASE, 'Pack purchase'),
        (TYPE_PLAN_GRANT, 'Plan grant'),
        (TYPE_DEBIT_DOCUMENT, 'Document generation'),
        (TYPE_DEBIT_AI, 'AI generation'),
        (TYPE_ROLLOVER, 'Rollover'),
        (TYPE_REFUND, 'Refund'),
        (TYPE_ADMIN_ADJUSTMENT, 'Admin adjustment'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='credit_transactions')
    transaction_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    amount = models.IntegerField(help_text='Signed: +credit / -debit.')
    balance_after = models.IntegerField()
    reference_id = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self):
        return f"{self.transaction_type} {self.amount:+d} -> {self.balance_after}"


class CreditPack(models.Model):
    """Admin-managed one-time credit bundle."""

    name = models.CharField(max_length=100)
    credits = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'price']

    def __str__(self):
        return f"{self.name} ({self.credits} cr / {self.price} {self.currency})"


class Plan(models.Model):
    """Recurring subscription tier."""

    MONTHLY = 'monthly'
    YEARLY = 'yearly'
    CYCLE_CHOICES = [(MONTHLY, 'Monthly'), (YEARLY, 'Yearly')]

    tier = models.CharField(max_length=50)
    billing_cycle = models.CharField(max_length=10, choices=CYCLE_CHOICES, default=MONTHLY)
    currency = models.CharField(max_length=3, default='INR')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    credits_per_cycle = models.IntegerField(default=0)
    razorpay_plan_id = models.CharField(max_length=80, blank=True)
    recurring_scheduling = models.BooleanField(default=False)
    max_smtp_configs = models.IntegerField(default=1)
    full_analytics = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'price']

    def __str__(self):
        return f"{self.tier} ({self.price} {self.currency}/{self.billing_cycle})"

    def create_gateway_plan(self):
        """Create the Razorpay plan matching this plan's current price and cycle
        and return its id (the caller stores it). Raises gateway.GatewayError."""
        from . import gateway
        return gateway.create_plan(f"SendGlob {self.tier} ({self.get_billing_cycle_display()})",
                                   self.price, self.currency, self.billing_cycle)

    def ensure_gateway_plan(self):
        """Link a Razorpay plan on first use when the gateway is live and none is
        linked yet (e.g. plans seeded before the keys were configured). The row
        lock stops two concurrent checkouts creating two gateway plans. Returns
        the id (blank in manual mode). Raises gateway.GatewayError."""
        from . import gateway
        if self.razorpay_plan_id or not gateway.is_live():
            return self.razorpay_plan_id
        with transaction.atomic():
            locked = Plan.objects.select_for_update().get(pk=self.pk)
            if not locked.razorpay_plan_id:
                locked.razorpay_plan_id = locked.create_gateway_plan()
                locked.save(update_fields=['razorpay_plan_id'])
        self.razorpay_plan_id = locked.razorpay_plan_id
        return self.razorpay_plan_id


class SubscriptionQuerySet(models.QuerySet):
    def current(self):
        """The subscription a user is billed on: active, or active-but-failing
        (past due). A user holds at most one; checkout enforces it."""
        return self.filter(status__in=[Subscription.ACTIVE, Subscription.PAST_DUE])


class Subscription(models.Model):
    """A user's recurring plan state."""

    ACTIVE = 'active'
    CANCELLED = 'cancelled'
    PAST_DUE = 'past_due'
    EXPIRED = 'expired'
    PENDING = 'pending'
    STATUS_CHOICES = [
        (ACTIVE, 'Active'), (CANCELLED, 'Cancelled'), (PAST_DUE, 'Past due'),
        (EXPIRED, 'Expired'), (PENDING, 'Pending'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    razorpay_subscription_id = models.CharField(max_length=80, blank=True, db_index=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    # Gateway plan the subscription is billed on. Plans are repriced by creating
    # a new gateway plan, so existing subscribers keep the id (and price) they
    # signed up with; this is what webhook payloads are compared against.
    gateway_plan_id = models.CharField(max_length=80, blank=True)
    # A plan switch the user requested, applied at the next renewal (no
    # mid-cycle proration). ``scheduled_gateway_plan_id`` identifies the switch
    # in the renewal webhook even if the target plan is repriced meanwhile.
    scheduled_plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='scheduled_subscriptions')
    scheduled_gateway_plan_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SubscriptionQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Sub({self.user_id}) {self.plan.tier} [{self.status}]"

    @property
    def is_manual(self):
        return self.razorpay_subscription_id.startswith('manual_')


class Payment(models.Model):
    """One row per gateway payment. Created only by the webhook handler."""

    PACK = 'pack'
    PLAN = 'plan'
    TYPE_CHOICES = [(PACK, 'Credit pack'), (PLAN, 'Plan')]

    CAPTURED = 'captured'
    FAILED = 'failed'
    PARTIALLY_REFUNDED = 'part_refund'
    REFUNDED = 'refunded'
    STATUS_CHOICES = [(CAPTURED, 'Captured'), (FAILED, 'Failed'),
                      (PARTIALLY_REFUNDED, 'Partially refunded'), (REFUNDED, 'Refunded')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='payments')
    payment_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    razorpay_payment_id = models.CharField(max_length=80, unique=True)
    razorpay_order_id = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=CAPTURED)
    credits_granted = models.IntegerField(default=0)
    # Human-readable item snapshot ("Starter 100 credit pack") so receipts and
    # history stay accurate after the catalogue is renamed or repriced.
    description = models.CharField(max_length=200, blank=True)
    pack = models.ForeignKey(CreditPack, null=True, blank=True, on_delete=models.PROTECT)
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment {self.razorpay_payment_id} {self.amount} {self.currency}"

    @property
    def is_manual(self):
        return self.razorpay_payment_id.startswith('manual_')


class Refund(models.Model):
    """One gateway refund against a Payment (a payment may be refunded in parts).

    Keyed on the gateway refund id so a replayed ``refund.processed`` event is a
    no-op. ``credits_reversed`` records what was actually clawed back, which can
    be less than owed when the user already spent the credits."""

    payment = models.ForeignKey('Payment', on_delete=models.PROTECT, related_name='refunds')
    razorpay_refund_id = models.CharField(max_length=80, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    credits_owed = models.IntegerField(default=0)
    credits_reversed = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Refund {self.razorpay_refund_id} {self.amount}"


class PackOrder(models.Model):
    """One checkout attempt for a credit pack.

    Snapshots the price and credits at the moment the order is opened, so
    fulfilment never trusts browser input, gateway notes, or a catalogue that may
    have been repriced since. Fulfilment (webhook, checkout verification, or the
    manual operator command) is keyed on this row and is idempotent.
    """

    CREATED = 'created'
    PAID = 'paid'
    FAILED = 'failed'
    EXPIRED = 'expired'
    STATUS_CHOICES = [(CREATED, 'Awaiting payment'), (PAID, 'Paid'),
                      (FAILED, 'Failed'), (EXPIRED, 'Expired')]

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='pack_orders')
    pack = models.ForeignKey(CreditPack, on_delete=models.PROTECT, related_name='orders')
    credits = models.IntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    gateway_order_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=CREATED)
    payment = models.OneToOneField('Payment', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='pack_order')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f"PackOrder {self.reference} [{self.status}]"

    @property
    def short_reference(self):
        """Compact reference users quote for manual payments."""
        return str(self.reference).split('-')[0].upper()


class Receipt(models.Model):
    """Plain payment receipt (not a tax invoice until GST registration)."""

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name='receipt')
    number = models.CharField(max_length=30, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    billing_name = models.CharField(max_length=200, blank=True)
    billing_address = models.TextField(blank=True)
    billing_country = models.CharField(max_length=2, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_id_seller = models.CharField(max_length=30, blank=True)
    description = models.CharField(max_length=200, blank=True)
    is_tax_invoice = models.BooleanField(default=False)
    pdf = models.FileField(upload_to='receipts/', blank=True)

    class Meta:
        ordering = ['-issued_at']

    def __str__(self):
        return self.number


class WebhookEvent(models.Model):
    """Idempotency store for gateway webhooks. Always store, then process."""

    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f"{self.event_type}:{self.event_id}"


def get_or_create_wallet(user):
    """Return the user's wallet, creating it on first access (idempotent)."""
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    return wallet


class ReceiptCounter(models.Model):
    """Monotonic per-year counter for gap-free receipt numbering.

    A single row per year, incremented under a row lock so concurrent payment
    captures never collide or skip a number (billing plan s.7.1).
    """

    year = models.IntegerField(unique=True)
    last_number = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.year}: {self.last_number}"
