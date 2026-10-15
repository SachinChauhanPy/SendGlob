import uuid
import logging

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from cryptography.fernet import Fernet

from core.models import SoftDeleteModel

logger = logging.getLogger(__name__)


class CustomUserManager(BaseUserManager):
    """
    Custom manager that uses *email* as the unique identifier for
    authentication instead of the inherited username field.

    AbstractUser still carries a username column (unique=True), so we
    generate a random UUID-based slug for it on every new user.  This
    satisfies the database constraint without coupling the two columns,
    which was the root cause of B-005 / B-018.
    """

    @staticmethod
    def _make_username():
        return uuid.uuid4().hex[:30]

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email address is required.')
        email = self.normalize_email(email)
        # Assign a random username unless the caller explicitly provides one
        # (e.g. existing tests that still pass username= as a keyword arg).
        extra_fields.setdefault('username', self._make_username())
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if not extra_fields['is_staff']:
            raise ValueError('Superuser must have is_staff=True.')
        if not extra_fields['is_superuser']:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    # Use email as the primary login field.
    email = models.EmailField(unique=True)

    # IANA timezone the user schedules in. Schedule times entered in the UI are
    # interpreted in this zone and converted to UTC for storage/firing (B-038).
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        help_text="IANA timezone used to interpret your campaign schedule times.",
    )

    # --- Profile identity (billing + display). All optional; safe on existing
    # rows. `country` (ISO-3166 alpha-2) is the authoritative pricing region when
    # set, falling back to header-based detection in core.pricing when blank. ---
    full_name = models.CharField(max_length=150, blank=True)
    organization = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO country code; drives pricing region.")

    USERNAME_FIELD = 'email'
    # username is inherited from AbstractUser and auto-populated by
    # CustomUserManager; it must not appear in REQUIRED_FIELDS or
    # `createsuperuser` / allauth signup will prompt for it needlessly.
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def save(self, *args, **kwargs):
        # Allauth social signup calls user.save() directly, bypassing
        # CustomUserManager.create_user(), so the UUID username is never
        # assigned.  Guard here to ensure every user always has a unique
        # username regardless of the creation path.
        if not self.username:
            self.username = CustomUserManager._make_username()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
    # NOTE: the save() override that previously forced self.username = self.email[:150]
    # has been removed.  That coupling caused UNIQUE constraint errors whenever
    # allauth tried to create a new local user for an email that already existed
    # as a password account (B-005 / B-018).  CustomUserManager now assigns a
    # random UUID username, and save() above provides the same guarantee for
    # social signup paths that bypass the manager.


class SMTPConfig(SoftDeleteModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='smtp_configs')
    name = models.CharField(max_length=100, default='My SMTP')
    smtp_host = models.CharField(max_length=255, default='smtp.gmail.com')
    smtp_port = models.IntegerField(default=587)
    smtp_username = models.EmailField(help_text="Your sender email address")
    encrypted_password = models.TextField(help_text="Encrypted SMTP password or app password")
    use_tls = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, help_text="Set as active configuration for sending mail")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMTP Configuration"
        verbose_name_plural = "SMTP Configurations"

    def __str__(self):
        return f"{self.name} ({self.smtp_username})"

    def save(self, *args, **kwargs):
        # If this is set to active, deactivate all other SMTP configurations for this user
        if self.is_active:
            SMTPConfig.active.filter(user=self.user, is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    @property
    def decryptor(self):
        key = settings.SMTP_ENCRYPTION_KEY
        if isinstance(key, str):
            key = key.encode()
        return Fernet(key)

    def set_app_password(self, raw_password):
        """Encrypt and store the SMTP password or app password."""
        if not raw_password:
            raise ValueError("App password cannot be empty.")
        self.encrypted_password = self.decryptor.encrypt(raw_password.encode()).decode()

    def get_app_password(self):
        """Decrypt and return the SMTP password or app password."""
        if not self.encrypted_password:
            return ""
        try:
            return self.decryptor.decrypt(self.encrypted_password.encode()).decode()
        except Exception as e:
            logger.error(f"Error decrypting app password for {self.smtp_username}: {e}")
            return ""
