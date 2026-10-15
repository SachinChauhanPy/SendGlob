"""
One-off migration helper: encrypt existing signature image files in place.

After encryption at rest is enabled (B-048), signature files written before the
change are still plaintext on disk and would fail to decrypt when opened. This
command rewrites each signature's stored files as Fernet ciphertext, skipping any
that are already encrypted, so it is safe to run more than once.

Run after deploying the encrypted storage and applying migrations:

    uv run python manage.py encrypt_signature_files
"""
from cryptography.fernet import Fernet, InvalidToken

from django.conf import settings
from django.core.management.base import BaseCommand


def _fernet():
    key = settings.FILE_ENCRYPTION_KEY
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class Command(BaseCommand):
    help = "Encrypt existing signature image files in place (idempotent)."

    def handle(self, *args, **options):
        from documents.models import Signature

        fernet = _fernet()
        encrypted = skipped = missing = 0

        # Iterate the base manager so soft-deleted rows are covered too; their
        # files may still exist on disk until the purge runs.
        for signature in Signature.objects.all():
            for field in (signature.original_image, signature.processed_image):
                if not field or not field.name:
                    continue
                try:
                    path = field.path
                except (NotImplementedError, ValueError):
                    continue
                try:
                    with open(path, 'rb') as fh:
                        raw = fh.read()
                except FileNotFoundError:
                    missing += 1
                    continue
                try:
                    fernet.decrypt(raw)
                    skipped += 1  # already ciphertext
                    continue
                except InvalidToken:
                    pass
                with open(path, 'wb') as fh:
                    fh.write(fernet.encrypt(raw))
                encrypted += 1

        self.stdout.write(self.style.SUCCESS(
            f"Encrypted {encrypted} file(s); skipped {skipped} already-encrypted; "
            f"{missing} missing on disk."
        ))
