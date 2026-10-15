"""Credit debit/credit service. The only place credits move.

Every mutation posts one append-only ``CreditTransaction`` and updates the
cached wallet balance inside a single DB transaction, locking the wallet row with
``select_for_update`` so two concurrent workers can never overdraw.
"""
from django.db import transaction

from .models import CreditTransaction, CreditWallet, get_or_create_wallet


class InsufficientCreditsError(Exception):
    """Raised when a debit would take the balance below zero."""

    def __init__(self, required, available):
        self.required = required
        self.available = available
        super().__init__(f"Insufficient credits: need {required}, have {available}.")


def _apply(user, *, signed_amount, transaction_type, reference_id='', note=''):
    with transaction.atomic():
        wallet = (CreditWallet.objects.select_for_update()
                  .get_or_create(user=user)[0])
        new_balance = wallet.balance + signed_amount
        if new_balance < 0:
            raise InsufficientCreditsError(-signed_amount, wallet.balance)
        wallet.balance = new_balance
        if signed_amount >= 0:
            wallet.lifetime_earned += signed_amount
        else:
            wallet.lifetime_spent += -signed_amount
        wallet.save(update_fields=['balance', 'lifetime_earned', 'lifetime_spent', 'updated_at'])
        return CreditTransaction.objects.create(
            user=user,
            transaction_type=transaction_type,
            amount=signed_amount,
            balance_after=new_balance,
            reference_id=reference_id or '',
            note=note or '',
        )


def credit(user, amount, *, transaction_type, reference_id='', note=''):
    """Add ``amount`` (positive) credits."""
    if amount <= 0:
        raise ValueError('credit() amount must be positive.')
    return _apply(user, signed_amount=amount, transaction_type=transaction_type,
                  reference_id=reference_id, note=note)


def debit(user, amount, *, transaction_type, reference_id='', note=''):
    """Remove ``amount`` (positive) credits; raise InsufficientCreditsError if
    the balance is too low."""
    if amount <= 0:
        raise ValueError('debit() amount must be positive.')
    return _apply(user, signed_amount=-amount, transaction_type=transaction_type,
                  reference_id=reference_id, note=note)


def adjust(user, amount, *, note='', reference_id=''):
    """Operator adjustment (signed). Positive grants, negative claws back (but
    cannot overdraw). Used by the ops console grant action."""
    if amount == 0:
        raise ValueError('adjust() amount must be non-zero.')
    return _apply(user, signed_amount=amount,
                  transaction_type=CreditTransaction.TYPE_ADMIN_ADJUSTMENT,
                  reference_id=reference_id, note=note)


def can_afford(user, amount):
    wallet = get_or_create_wallet(user)
    return wallet.balance >= amount


def balance(user):
    return get_or_create_wallet(user).balance
