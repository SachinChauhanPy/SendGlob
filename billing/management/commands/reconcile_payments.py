"""Reconcile the money trail and report discrepancies."""
from django.core.management.base import BaseCommand

from billing import accounting, receipts
from billing.models import Payment


class Command(BaseCommand):
    help = 'Cross-check payments, receipts and webhook events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-missing-receipts',
            action='store_true',
            help='Automatically issue receipts for captured payments that lack one.',
        )

    def handle(self, *args, **options):
        if options.get('fix_missing_receipts'):
            fixed = 0
            for p in Payment.objects.filter(status__in=accounting.SETTLED_STATUSES,
                                            receipt__isnull=True):
                receipts.issue_receipt(p)
                fixed += 1
            if fixed:
                self.stdout.write(self.style.SUCCESS(f"Issued {fixed} missing receipt(s)."))

        issues = accounting.reconcile()
        if not issues:
            self.stdout.write(self.style.SUCCESS('No discrepancies found.'))
            return
        for issue in issues:
            self.stdout.write(self.style.WARNING(issue))
        self.stdout.write(f"{len(issues)} issue(s).")
