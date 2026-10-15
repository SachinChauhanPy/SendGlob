"""Receipt issuance: gap-free sequential numbering, billing snapshot, and a PDF.

A receipt is created automatically when a Payment is captured (from the webhook
handler). Numbering is gap-free and collision-free under concurrency via a locked
per-year counter row. Until GST registration the receipt is not a tax invoice.
"""
import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from . import conf, pdfwriter
from .models import BillingProfile, Receipt, ReceiptCounter

logger = logging.getLogger(__name__)


def next_receipt_number():
    """Allocate the next gap-free receipt number, e.g. SG-2026-000123.

    The per-year counter row is locked for the duration so two concurrent captures
    cannot read the same value (the lock is a no-op on SQLite, which serializes
    writes anyway; it is correct on Postgres, the eventual production DB)."""
    year = timezone.now().year
    with transaction.atomic():
        counter, _ = ReceiptCounter.objects.select_for_update().get_or_create(year=year)
        counter.last_number += 1
        counter.save(update_fields=['last_number'])
        seq = counter.last_number
    return f"{conf.receipt_prefix()}-{year}-{seq:06d}"


def issue_receipt(payment):
    """Create and return the Receipt for a captured Payment (idempotent: returns
    the existing receipt if one already exists)."""
    existing = Receipt.objects.filter(payment=payment).first()
    if existing:
        return existing

    profile = BillingProfile.objects.filter(user=payment.user).first()
    billing_name = (profile.billing_name if profile and profile.billing_name
                    else (payment.user.full_name or payment.user.email))
    billing_address = profile.address_block() if profile else ''
    billing_country = (profile.country if profile and profile.country
                       else getattr(payment.user, 'country', '') or '')

    seller = conf.seller_details()
    receipt = Receipt.objects.create(
        payment=payment,
        number=next_receipt_number(),
        billing_name=billing_name,
        billing_address=billing_address,
        billing_country=billing_country,
        amount=payment.amount,
        currency=payment.currency,
        tax_id_seller=seller['tax_id'],
        description=payment.description or payment.get_payment_type_display(),
        is_tax_invoice=False,
    )
    render_pdf(receipt)
    return receipt


def render_pdf(receipt):
    """(Re)render the receipt PDF from its own snapshot fields. Returns True on
    success. Rendering failures are logged, never raised: a receipt record
    without a PDF is still valid and the download view retries rendering."""
    payment = receipt.payment
    seller = conf.seller_details()
    lines = [seller['name']]
    lines += [ln for ln in seller['address'].splitlines() if ln.strip()]
    if seller['email']:
        lines.append(seller['email'])
    if receipt.tax_id_seller:
        lines.append(f"Tax ID: {receipt.tax_id_seller}")
    lines += [
        "",
        f"Receipt number: {receipt.number}",
        f"Issued: {receipt.issued_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"Billed to: {receipt.billing_name}",
    ]
    lines += receipt.billing_address.splitlines()
    lines += [
        "",
        f"Item: {receipt.description}",
        f"Credits granted: {payment.credits_granted}",
        f"Payment reference: {payment.razorpay_payment_id}",
        "",
        f"Amount paid: {receipt.amount} {receipt.currency}",
        "",
        "This is a payment receipt, not a tax invoice.",
    ]
    try:
        pdf_bytes = pdfwriter.render_lines(lines, title='Payment Receipt')
        receipt.pdf.save(f"{receipt.number}.pdf", ContentFile(pdf_bytes), save=True)
        return True
    except Exception:
        logger.exception("Failed to render PDF for receipt %s", receipt.number)
        return False
