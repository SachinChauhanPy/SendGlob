"""Activate or renew a plan for one billing cycle after a manual payment."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from billing import fulfillment
from billing.models import Plan, Subscription


class Command(BaseCommand):
    help = ('Record a manual plan payment: activates (or renews) the user\'s plan for one '
            'cycle, grants the cycle credits and issues a receipt. Idempotent per payment '
            'reference.')

    def add_arguments(self, parser):
        parser.add_argument('email')
        parser.add_argument('plan_id', type=int)
        parser.add_argument('payment_reference',
                            help='Your reference for the received payment, e.g. a bank UTR.')

    def handle(self, *args, email, plan_id, payment_reference, **options):
        user = get_user_model().objects.filter(email=email).first()
        if user is None:
            raise CommandError(f"No user with email {email}.")
        plan = Plan.objects.filter(pk=plan_id).first()
        if plan is None:
            raise CommandError(f"No plan with id {plan_id}.")
        current = Subscription.objects.current().filter(user=user).first()
        switching = bool(current and current.plan_id != plan.pk)
        if switching and current.scheduled_plan_id != plan.pk:
            raise CommandError(f"{email} is on the {current.plan.tier} plan and has not "
                               f"requested a switch to {plan.tier}.")
        if current and not current.is_manual:
            raise CommandError(f"{email} is billed through the gateway; renewals arrive by webhook.")

        sub = current or (Subscription.objects
                          .filter(user=user, plan=plan, status=Subscription.PENDING,
                                  razorpay_subscription_id__startswith='manual_')
                          .first())
        if sub is None:
            sub = Subscription.objects.create(
                user=user, plan=plan, status=Subscription.PENDING,
                razorpay_subscription_id=f"manual_sub_u{user.pk}_{payment_reference.strip()}"[:80])
        # A renewal starts where the paid period ends, so paying early never
        # shortens the plan.
        start = sub.current_period_end if current else None
        payment = fulfillment.record_subscription_charge(
            sub, f"manual_{payment_reference.strip()}", period_start=start,
            period_end=fulfillment.default_period_end(start, plan) if start else None,
            apply_scheduled_change=switching)
        sub.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f"{plan.tier} active for {email} until {sub.current_period_end:%Y-%m-%d}; "
            f"receipt {payment.receipt.number}."))
