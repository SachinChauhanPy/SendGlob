"""
Encryption-at-rest storage for sensitive user files.

``EncryptedFileSystemStorage`` transparently Fernet-encrypts file contents on
write and decrypts them on read, so callers (the background-removal task, the
rendering engine, the serving views) always see plaintext. It is applied only to
sensitive fields, currently the signature images (B-048).

Because files are ciphertext on disk, they must NEVER be served directly by a
static web server, which would return unreadable bytes. They are delivered only
through the ownership-checked views that call ``FieldFile.open()`` (see
``docs/media-security.md`` and B-047). Encryption and access control are
therefore complementary parts of the same guarantee.
"""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from cryptography.fernet import Fernet


class EncryptedFileSystemStorage(FileSystemStorage):
    """Filesystem storage that encrypts contents at rest with Fernet."""

    @property
    def _fernet(self):
        # Read the key lazily so importing this module never requires it; the key
        # is only needed when a file is actually written or read.
        key = settings.FILE_ENCRYPTION_KEY
        if isinstance(key, str):
            key = key.encode()
        return Fernet(key)

    def _save(self, name, content):
        try:
            content.seek(0)
        except (AttributeError, OSError):
            pass
        token = self._fernet.encrypt(content.read())
        return super()._save(name, ContentFile(token))

    def _open(self, name, mode='rb'):
        with super()._open(name, 'rb') as encrypted:
            token = encrypted.read()
        return ContentFile(self._fernet.decrypt(token), name=name)


# Module-level instance plus a callable accessor. FileField/ImageField are given
# the *callable*, which keeps the storage out of serialized migration state and
# lets the same singleton back every field.
encrypted_storage = EncryptedFileSystemStorage()


def encrypted_media_storage():
    return encrypted_storage
