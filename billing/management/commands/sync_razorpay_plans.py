"""Create Razorpay plans for active local plans that are not linked to one."""
from django.core.management.base import BaseCommand, CommandError

from billing import gateway
from billing.models import Plan


class Command(BaseCommand):
    help = 'Create and link a Razorpay plan for every active Plan without razorpay_plan_id.'

    def handle(self, *args, **options):
        if not gateway.is_live():
            raise CommandError('Razorpay is not configured (keys and the razorpay package).')
        plans = Plan.objects.filter(is_active=True, razorpay_plan_id='')
        if not plans:
            self.stdout.write(self.style.SUCCESS('All active plans are linked.'))
            return
        for plan in plans:
            try:
                plan.ensure_gateway_plan()
            except gateway.GatewayError as exc:
                raise CommandError(f"Failed on {plan.tier}: {exc}")
            self.stdout.write(self.style.SUCCESS(f"Linked {plan.tier} -> {plan.razorpay_plan_id}"))
