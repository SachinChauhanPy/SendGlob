from django import forms
from django.contrib import admin

from . import gateway
from .models import (BillingProfile, CreditPack, CreditTransaction, CreditWallet, PackOrder,
                     Payment, Plan, Receipt, Refund, Subscription, WebhookEvent)


class _ReadOnlyFinancial(admin.ModelAdmin):
    """Financial rows are immutable: visible but never added/edited/deleted."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditWallet)
class CreditWalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'lifetime_earned', 'lifetime_spent', 'updated_at')
    search_fields = ('user__email',)
    readonly_fields = ('balance', 'lifetime_earned', 'lifetime_spent')


@admin.register(CreditTransaction)
class CreditTransactionAdmin(_ReadOnlyFinancial):
    list_display = ('created_at', 'user', 'transaction_type', 'amount', 'balance_after', 'reference_id')
    list_filter = ('transaction_type', 'created_at')
    search_fields = ('user__email', 'note', 'reference_id')
    date_hierarchy = 'created_at'


@admin.register(Payment)
class PaymentAdmin(_ReadOnlyFinancial):
    list_display = ('created_at', 'user', 'description', 'amount', 'currency', 'status', 'credits_granted')
    list_filter = ('payment_type', 'status', 'currency')
    search_fields = ('user__email', 'razorpay_payment_id', 'razorpay_order_id')


@admin.register(Refund)
class RefundAdmin(_ReadOnlyFinancial):
    list_display = ('created_at', 'razorpay_refund_id', 'payment', 'amount', 'credits_owed',
                    'credits_reversed')
    search_fields = ('razorpay_refund_id', 'payment__razorpay_payment_id', 'payment__user__email')


@admin.register(PackOrder)
class PackOrderAdmin(_ReadOnlyFinancial):
    list_display = ('created_at', 'reference', 'user', 'pack', 'amount', 'currency', 'status')
    list_filter = ('status', 'currency')
    search_fields = ('reference', 'gateway_order_id', 'user__email')


@admin.register(Receipt)
class ReceiptAdmin(_ReadOnlyFinancial):
    list_display = ('number', 'issued_at', 'amount', 'currency', 'is_tax_invoice')
    search_fields = ('number', 'billing_name')


class _CatalogueAdmin(admin.ModelAdmin):
    """Catalogue items are referenced by historical orders and payments, so they
    are retired with ``is_active`` rather than deleted. Price edits are safe:
    orders and payments snapshot the price at purchase time."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditPack)
class CreditPackAdmin(_CatalogueAdmin):
    list_display = ('name', 'credits', 'price', 'currency', 'is_active', 'display_order')
    list_editable = ('is_active', 'display_order')


class PlanAdminForm(forms.ModelForm):
    """Keeps the gateway plan in step with the local price.

    Razorpay plans are immutable, so in live mode a new gateway plan is created
    whenever price, currency or cycle changes (or none is linked yet). Existing
    subscribers stay on the gateway plan they bought, at the old price; new
    checkouts and plan switches use the new one. An id typed in by the operator
    (linking a plan created in the Razorpay dashboard) is kept as given."""

    PRICING_FIELDS = ('price', 'currency', 'billing_cycle')

    class Meta:
        model = Plan
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        if self.errors or not gateway.is_live():
            return cleaned
        if 'razorpay_plan_id' in self.changed_data and cleaned.get('razorpay_plan_id'):
            return cleaned
        pricing_changed = any(f in self.changed_data for f in self.PRICING_FIELDS)
        if cleaned.get('razorpay_plan_id') and not pricing_changed:
            return cleaned
        draft = Plan(tier=cleaned['tier'], price=cleaned['price'], currency=cleaned['currency'],
                     billing_cycle=cleaned['billing_cycle'])
        try:
            cleaned['razorpay_plan_id'] = draft.create_gateway_plan()
        except gateway.GatewayError as exc:
            raise forms.ValidationError(f"Could not create the Razorpay plan: {exc}")
        return cleaned


@admin.register(Plan)
class PlanAdmin(_CatalogueAdmin):
    form = PlanAdminForm
    list_display = ('tier', 'price', 'currency', 'billing_cycle', 'credits_per_cycle',
                    'razorpay_plan_id', 'is_active')
    list_editable = ('is_active',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'current_period_end', 'cancel_at_period_end')
    list_filter = ('status',)
    search_fields = ('user__email',)


@admin.register(BillingProfile)
class BillingProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'billing_name', 'country', 'updated_at')
    search_fields = ('user__email', 'billing_name')


@admin.register(WebhookEvent)
class WebhookEventAdmin(_ReadOnlyFinancial):
    list_display = ('received_at', 'event_type', 'event_id', 'processed_at')
    list_filter = ('event_type',)
    search_fields = ('event_id',)
