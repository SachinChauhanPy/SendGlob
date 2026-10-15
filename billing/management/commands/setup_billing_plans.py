"""Seed default credit packs and plans (idempotent). Edit prices in the admin."""
from django.core.management.base import BaseCommand

from billing.models import CreditPack, Plan


PACKS = [
    {'name': 'Starter 100', 'credits': 100, 'price': 199, 'currency': 'INR', 'display_order': 1},
    {'name': 'Growth 500', 'credits': 500, 'price': 799, 'currency': 'INR', 'display_order': 2},
    {'name': 'Pro 2000', 'credits': 2000, 'price': 2499, 'currency': 'INR', 'display_order': 3},
]

PLANS = [
    {'tier': 'Creator', 'price': 499, 'currency': 'INR', 'credits_per_cycle': 1000,
     'recurring_scheduling': True, 'max_smtp_configs': 3, 'full_analytics': True,
     'priority_support': False, 'display_order': 1},
    {'tier': 'Business', 'price': 1499, 'currency': 'INR', 'credits_per_cycle': 5000,
     'recurring_scheduling': True, 'max_smtp_configs': 10, 'full_analytics': True,
     'priority_support': True, 'display_order': 2},
]


class Command(BaseCommand):
    help = 'Create default credit packs and subscription plans if missing.'

    def handle(self, *args, **options):
        for p in PACKS:
            obj, created = CreditPack.objects.get_or_create(name=p['name'], defaults=p)
            self.stdout.write(('Created' if created else 'Exists') + f" pack {obj.name}")
        for p in PLANS:
            obj, created = Plan.objects.get_or_create(tier=p['tier'], billing_cycle='monthly', defaults=p)
            self.stdout.write(('Created' if created else 'Exists') + f" plan {obj.tier}")
