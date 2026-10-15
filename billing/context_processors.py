"""Inject the wallet balance, tier and a low-balance flag into every template so
the UI can show credits and an upgrade nudge."""
from . import gating
from .models import CreditWallet

LOW_BALANCE_THRESHOLD = 10


def billing_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'billing': None}
    wallet = CreditWallet.objects.filter(user=user).first()
    balance = wallet.balance if wallet else 0
    tier, _plan = gating.get_tier(user)
    return {'billing': {
        'balance': balance,
        'tier': tier,
        'low_balance': balance <= LOW_BALANCE_THRESHOLD,
    }}
