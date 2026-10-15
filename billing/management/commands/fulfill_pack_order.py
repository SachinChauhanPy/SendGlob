"""Fulfil a manual-mode credit pack order once payment has been received."""
from django.core.management.base import BaseCommand, CommandError

from billing import fulfillment
from billing.models import PackOrder


class Command(BaseCommand):
    help = ('Mark a pack order paid (manual payments): records the payment, grants the '
            'credits snapshotted on the order and issues a receipt. Idempotent.')

    def add_arguments(self, parser):
        parser.add_argument('reference', help='Order reference shown to the user (short or full).')
        parser.add_argument('payment_reference',
                            help='Your reference for the received payment, e.g. a bank UTR.')

    def handle(self, *args, reference, payment_reference, **options):
        matches = list(PackOrder.objects.filter(reference__istartswith=reference.strip().lower())[:2])
        if not matches:
            raise CommandError(f"No pack order with reference {reference}.")
        if len(matches) > 1:
            raise CommandError(f"Reference {reference} is ambiguous; use the full reference.")
        order = matches[0]
        if order.status == PackOrder.PAID:
            raise CommandError(f"Order {order.short_reference} is already paid "
                               f"({order.payment.razorpay_payment_id}).")
        payment = fulfillment.fulfill_pack_order(order, f"manual_{payment_reference.strip()}")
        self.stdout.write(self.style.SUCCESS(
            f"Order {order.short_reference} paid: {order.credits} credits to {order.user.email}, "
            f"receipt {payment.receipt.number}."))
