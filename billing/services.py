"""High-level credit operations reused by the ops console and billing UI.

Thin wrappers over ``billing.wallet`` that keep callers decoupled from wallet
internals and preserve the ops console's ``grant_credits(user, amount, actor,
reason)`` signature.
"""
from . import wallet
from .models import CreditTransaction


def grant_credits(user, amount, *, actor=None, reason='', kind=None):
    """Operator grant/adjustment. ``amount`` signed; positive grants, negative
    claws back (cannot overdraw). Returns the CreditTransaction."""
    if amount == 0:
        raise ValueError('Grant amount must be non-zero.')
    note = reason or ''
    if actor is not None:
        note = (f"{note} (by {getattr(actor, 'email', actor)})").strip()
    return wallet.adjust(user, amount, note=note[:255])


def debit_credits(user, amount, *, reason='', reference_id=''):
    """Usage debit (positive amount). Raises InsufficientCreditsError if low."""
    return wallet.debit(user, amount, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT,
                        reference_id=reference_id, note=reason)
