"""Backfill a CreditWallet (with the one-time free grant) for every existing user,
so no account is left wallet-less after billing is introduced. Idempotent and a
no-op on a fresh database."""
from django.conf import settings
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0],
                          settings.AUTH_USER_MODEL.split('.')[1])
    CreditWallet = apps.get_model('billing', 'CreditWallet')
    CreditTransaction = apps.get_model('billing', 'CreditTransaction')
    free = int(getattr(settings, 'BILLING_FREE_SIGNUP_CREDITS', 50))
    for user in User.objects.all():
        wallet, created = CreditWallet.objects.get_or_create(user=user)
        if created and free > 0 and not CreditTransaction.objects.filter(user=user).exists():
            wallet.balance = free
            wallet.lifetime_earned = free
            wallet.save()
            CreditTransaction.objects.create(
                user=user, transaction_type='signup_grant', amount=free,
                balance_after=free, note='Backfilled welcome credits')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('billing', '0002_initial')]
    operations = [migrations.RunPython(backfill, noop)]
