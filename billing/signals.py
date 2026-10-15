"""Create a wallet and grant the one-time free signup credits on user creation.

Idempotent: a wallet is only created once, and the free grant is only posted when
the wallet is brand new (balance 0 and no prior transactions), so re-saving a user
never re-grants.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import conf
from .models import CreditTransaction, CreditWallet


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid='billing_create_wallet')
def create_wallet_for_user(sender, instance, created, **kwargs):
    if not created:
        return
    wallet, was_created = CreditWallet.objects.get_or_create(user=instance)
    free = conf.free_signup_credits()
    if was_created and free > 0 and not CreditTransaction.objects.filter(user=instance).exists():
        from . import wallet as wallet_service
        wallet_service.credit(instance, free,
                              transaction_type=CreditTransaction.TYPE_SIGNUP_GRANT,
                              note='Welcome free credits')
