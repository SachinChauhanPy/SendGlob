"""Grant credits to a user from the CLI (manual top-up path)."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from billing import wallet


class Command(BaseCommand):
    help = 'Grant credits to a user by email.'

    def add_arguments(self, parser):
        parser.add_argument('email')
        parser.add_argument('amount', type=int)
        parser.add_argument('--note', default='Manual grant')

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(email=options['email'])
        except User.DoesNotExist:
            raise CommandError(f"No user with email {options['email']}")
        try:
            txn = wallet.adjust(user, options['amount'], note=options['note'])
        except (ValueError, wallet.InsufficientCreditsError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Granted {options['amount']:+d} to {user.email}; balance {txn.balance_after}."))
