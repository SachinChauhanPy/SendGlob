"""Billing UI, checkout, and the Razorpay webhook endpoint.

User-facing pages are login-gated. Credits and plans are granted only through
``billing.fulfillment``, reached from either the signature-verified checkout
callback or the signature-verified webhook; whichever arrives first fulfils and
the other is a no-op. The webhook endpoint is CSRF-exempt. Receipt downloads are
ownership-checked (reusing the media-security pattern).
"""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import conf, fulfillment, gateway, gating, receipts, webhooks
from .forms import BillingAddressForm, BillingIdentityForm
from .models import (BillingProfile, CreditPack, CreditTransaction, PackOrder, Payment, Plan,
                     Receipt, Subscription)

logger = logging.getLogger(__name__)

GATEWAY_UNAVAILABLE = ('The payment provider could not be reached. No charge was made; '
                       'please try again in a few minutes.')
PLAN_UNAVAILABLE = ('This plan cannot be purchased online right now. No charge was made; '
                    'please try again later or contact support.')


def _current_subscription(user):
    return Subscription.objects.current().filter(user=user).select_related('plan').first()


@login_required
def dashboard(request):
    tier, plan = gating.get_tier(request.user)
    start_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = (CreditTransaction.objects
             .filter(user=request.user, amount__lt=0, created_at__gte=start_month)
             .exclude(transaction_type=CreditTransaction.TYPE_REFUND)
             .aggregate(s=Sum('amount'))['s'] or 0)
    pending_orders = (PackOrder.objects.filter(user=request.user, status=PackOrder.CREATED)
                      .select_related('pack')[:5])
    context = {
        'tier': tier, 'plan': plan, 'subscription': _current_subscription(request.user),
        'credits_used_this_month': -spent,
        'pending_orders': pending_orders,
        'active_section': 'dashboard',
    }
    return render(request, 'billing/dashboard.html', context)


def _checkout_context(request, **extra):
    return {
        'razorpay_key_id': conf.razorpay_keys()[0],
        'seller_name': conf.seller_details()['name'],
        'user': request.user,
        **extra,
    }


@login_required
@require_POST
def checkout_pack(request, pk):
    """Open a PackOrder (price/credits snapshot) and its gateway order.

    Nothing is credited here. In manual mode the order stays open with a
    reference the operator fulfils after settling payment out of band."""
    pack = get_object_or_404(CreditPack, pk=pk, is_active=True)
    order = PackOrder.objects.create(user=request.user, pack=pack, credits=pack.credits,
                                     amount=pack.price, currency=pack.currency)
    try:
        gw_order = gateway.create_order(
            order.amount, order.currency, f"pack-{order.short_reference}",
            notes={'pack_order': str(order.reference), 'user_id': str(request.user.pk)})
    except gateway.GatewayError:
        fulfillment.mark_pack_order_failed(order)
        messages.error(request, GATEWAY_UNAVAILABLE)
        return redirect('billing:dashboard')
    is_live = not gw_order.get('stub')
    if is_live:
        order.gateway_order_id = gw_order['id']
        order.save(update_fields=['gateway_order_id'])
    return render(request, 'billing/checkout.html', _checkout_context(
        request, kind='pack', item=pack, order=order, is_live=is_live,
        amount=order.amount, currency=order.currency,
        amount_minor=gateway.to_minor_units(order.amount),
        gateway_id=order.gateway_order_id or '', active_section='packs'))


@login_required
@require_POST
def checkout_plan(request, pk):
    """Open a pending Subscription and its gateway subscription.

    A user holds one plan at a time: switching requires the current plan to end
    first, which avoids double billing and ambiguous tiers. Activation happens in
    fulfilment (checkout callback or ``subscription.charged``)."""
    plan = get_object_or_404(Plan, pk=pk, is_active=True)
    current = _current_subscription(request.user)
    if current:
        if current.plan_id == plan.pk:
            messages.info(request, f"You already have an active subscription to the {plan.tier} plan.")
        else:
            messages.info(request, f"You are subscribed to the {current.plan.tier} plan. Use "
                                   f"\"Switch\" below to move to {plan.tier} from your next renewal.")
        return redirect('billing:manage_subscription')

    try:
        plan.ensure_gateway_plan()
    except gateway.GatewayError:
        logger.exception('Could not link a Razorpay plan for plan %s', plan.pk)
        messages.error(request, PLAN_UNAVAILABLE)
        return redirect('billing:dashboard')
    try:
        gw_sub = gateway.create_subscription(
            plan.razorpay_plan_id, plan.billing_cycle,
            notes={'user_id': str(request.user.pk), 'plan_id': str(plan.pk)})
    except gateway.GatewayError:
        messages.error(request, GATEWAY_UNAVAILABLE)
        return redirect('billing:dashboard')
    sub = Subscription.objects.create(user=request.user, plan=plan, status=Subscription.PENDING,
                                      razorpay_subscription_id=gw_sub['id'],
                                      gateway_plan_id=plan.razorpay_plan_id)
    return render(request, 'billing/checkout.html', _checkout_context(
        request, kind='plan', item=plan, subscription=sub, is_live=not gw_sub.get('stub'),
        amount=plan.price, currency=plan.currency,
        gateway_id=sub.razorpay_subscription_id, active_section='plans'))


@login_required
@require_POST
def checkout_complete(request):
    """Razorpay checkout success callback (form POST from the checkout page).

    Verifies the gateway signature, then fulfils through the same idempotent
    path as the webhook, so the user sees credits or their plan immediately
    instead of waiting for webhook delivery. Records are always looked up by the
    signed id *and* the current user."""
    payment_id = request.POST.get('razorpay_payment_id', '')
    signature = request.POST.get('razorpay_signature', '')
    order_id = request.POST.get('razorpay_order_id', '')
    subscription_id = request.POST.get('razorpay_subscription_id', '')

    if order_id:
        order = PackOrder.objects.filter(gateway_order_id=order_id, user=request.user).first()
        if order and gateway.verify_payment_signature(order_id, payment_id, signature):
            fulfillment.fulfill_pack_order(order, payment_id)
            messages.success(request, f"Payment received. {order.credits} credits were added "
                                      "to your balance.")
            return redirect('billing:dashboard')
    elif subscription_id:
        sub = Subscription.objects.filter(razorpay_subscription_id=subscription_id,
                                          user=request.user).first()
        if sub and gateway.verify_subscription_signature(subscription_id, payment_id, signature):
            fulfillment.record_subscription_charge(sub, payment_id)
            messages.success(request, f"Your {sub.plan.tier} plan is now active.")
            return redirect('billing:dashboard')

    logger.warning('Checkout callback failed verification for user %s', request.user.pk)
    messages.error(request, 'We could not verify this payment. If you were charged, your '
                            'purchase will be applied automatically within a few minutes; '
                            'otherwise contact support.')
    return redirect('billing:dashboard')


@login_required
def history(request):
    txns = Paginator(CreditTransaction.objects.filter(user=request.user), 25)
    payments = (Payment.objects.filter(user=request.user)
                .select_related('receipt').order_by('-created_at')[:50])
    return render(request, 'billing/history.html', {
        'txns': txns.get_page(request.GET.get('page')),
        'payments': payments,
        'active_section': 'history',
    })


@login_required
def receipt_download(request, pk):
    """Serve a receipt PDF behind an ownership check (not a public URL)."""
    if request.user.is_staff:
        receipt = get_object_or_404(Receipt, pk=pk)
    else:
        receipt = get_object_or_404(Receipt, pk=pk, payment__user=request.user)
    if not receipt.pdf and not receipts.render_pdf(receipt):
        raise Http404
    return FileResponse(receipt.pdf.open('rb'), content_type='application/pdf',
                        filename=f"{receipt.number}.pdf")


@login_required
def manage_subscription(request):
    sub = _current_subscription(request.user)
    switchable = []
    if sub and sub.status == Subscription.ACTIVE and not sub.cancel_at_period_end:
        switchable = (Plan.objects.filter(is_active=True, currency=sub.plan.currency)
                      .exclude(pk=sub.plan_id))
    return render(request, 'billing/manage_subscription.html', {
        'subscription': sub, 'switchable_plans': switchable, 'active_section': 'manage'})


@login_required
@require_POST
def change_plan(request, pk):
    """Schedule a switch to another plan from the next renewal.

    Switching at renewal (rather than mid-cycle with proration) keeps every
    charge equal to a full plan price and every credit grant equal to a full
    cycle, so receipts and the ledger stay simple to audit. Users who need more
    credits now can buy a pack."""
    sub = _current_subscription(request.user)
    plan = get_object_or_404(Plan, pk=pk, is_active=True)
    if not sub or sub.status != Subscription.ACTIVE:
        messages.info(request, 'You need an active plan to switch plans.')
        return redirect('billing:manage_subscription')
    if sub.cancel_at_period_end:
        messages.info(request, 'Your plan is set to end; subscribe to the new plan after it ends.')
        return redirect('billing:manage_subscription')
    if plan.pk == sub.plan_id or plan.currency != sub.plan.currency:
        messages.error(request, 'That plan is not available as a switch from your current plan.')
        return redirect('billing:manage_subscription')
    try:
        if not sub.is_manual:
            plan.ensure_gateway_plan()
        gateway.schedule_plan_change(sub.razorpay_subscription_id, plan.razorpay_plan_id)
    except gateway.GatewayError:
        messages.error(request, 'We could not reach the payment provider. Nothing was changed; '
                                'please try again shortly.')
        return redirect('billing:manage_subscription')
    fulfillment.schedule_plan_change(sub, plan)
    when = (f"on {date_format(sub.current_period_end, 'M j, Y')}" if sub.current_period_end
            else 'at your next renewal')
    messages.success(request, f"You will move to the {plan.tier} plan {when}.")
    return redirect('billing:manage_subscription')


@login_required
@require_POST
def cancel_plan_change(request):
    sub = _current_subscription(request.user)
    if not sub or not sub.scheduled_plan_id:
        messages.info(request, 'There is no pending plan change.')
        return redirect('billing:manage_subscription')
    try:
        gateway.cancel_scheduled_plan_change(sub.razorpay_subscription_id)
    except gateway.GatewayError:
        messages.error(request, 'We could not reach the payment provider. Nothing was changed; '
                                'please try again shortly.')
        return redirect('billing:manage_subscription')
    fulfillment.clear_plan_change(sub)
    messages.success(request, f"You will stay on the {sub.plan.tier} plan.")
    return redirect('billing:manage_subscription')


@login_required
@require_POST
def cancel_subscription(request):
    """Stop renewal. An active plan runs to the end of the paid period; a past-due
    plan (renewal already failing) is cancelled immediately to stop retries.

    The local record only changes once the gateway confirms, so the UI can never
    claim a cancellation the customer will still be charged for."""
    sub = _current_subscription(request.user)
    if not sub:
        messages.info(request, 'No active subscription to cancel.')
        return redirect('billing:manage_subscription')
    if sub.cancel_at_period_end:
        messages.info(request, 'Your plan is already set to end at the close of this period.')
        return redirect('billing:manage_subscription')

    at_period_end = sub.status == Subscription.ACTIVE
    try:
        gateway.cancel_subscription(sub.razorpay_subscription_id, cancel_at_period_end=at_period_end)
    except gateway.GatewayError:
        messages.error(request, 'We could not reach the payment provider to cancel your plan. '
                                'Nothing was changed; please try again shortly.')
        return redirect('billing:manage_subscription')

    # Cancelling supersedes any pending switch: there is no renewal to apply it to.
    sub.scheduled_plan = None
    sub.scheduled_gateway_plan_id = ''
    if at_period_end:
        sub.cancel_at_period_end = True
        sub.save(update_fields=['cancel_at_period_end', 'scheduled_plan',
                                'scheduled_gateway_plan_id', 'updated_at'])
        messages.success(request, 'Your plan will not renew at the end of the current period.')
    else:
        sub.status = Subscription.CANCELLED
        sub.save(update_fields=['status', 'scheduled_plan', 'scheduled_gateway_plan_id',
                                'updated_at'])
        messages.success(request, 'Your plan has been cancelled.')
    return redirect('billing:manage_subscription')


@login_required
def profile(request):
    """Edit billing identity (and the user profile fields used on receipts)."""
    bp, _ = BillingProfile.objects.get_or_create(user=request.user)
    identity = BillingIdentityForm(request.POST or None, instance=request.user)
    address = BillingAddressForm(request.POST or None, instance=bp)
    if request.method == 'POST' and identity.is_valid() and address.is_valid():
        with transaction.atomic():
            user = identity.save()
            bp = address.save(commit=False)
            bp.country = user.country
            bp.save()
        messages.success(request, 'Billing profile updated.')
        return redirect('billing:profile')
    return render(request, 'billing/profile.html',
                  {'identity_form': identity, 'address_form': address, 'active_section': 'profile'})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Razorpay webhook: verify signature, store-then-process idempotently."""
    signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE', '')
    body = request.body
    if not gateway.verify_webhook_signature(body, signature):
        return HttpResponse('invalid signature', status=400)
    try:
        payload = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('bad payload')
    event_id = request.META.get('HTTP_X_RAZORPAY_EVENT_ID', '') or payload.get('event_id', '')
    event_type = payload.get('event', '')
    if not event_id:
        return HttpResponseBadRequest('missing event id')
    result = webhooks.process_event(event_id, event_type, payload)
    return JsonResponse({'status': result})
