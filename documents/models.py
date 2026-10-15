import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings

from core.models import SoftDeleteModel
from core.storage import encrypted_media_storage


def _uuid_upload_to(subdir, filename):
    """Return ``<subdir>/<random-hex><ext>`` so a stored file's path cannot be
    guessed from the original filename (B-047). Sensitive images are served only
    through ownership-checked views, never by a predictable media URL."""
    ext = os.path.splitext(filename)[1].lower()
    return f"{subdir}/{uuid.uuid4().hex}{ext}"


def signature_original_upload_to(instance, filename):
    return _uuid_upload_to("signatures/originals", filename)


def signature_processed_upload_to(instance, filename):
    return _uuid_upload_to("signatures/processed", filename)


# Text alignment options shared by template-level (global) typography and the
# per-field typography on DocumentTextField.
ALIGN_CENTER = 'center'
ALIGN_LEFT = 'left'
ALIGN_RIGHT = 'right'
ALIGNMENT_CHOICES = [
    (ALIGN_CENTER, 'Center'),
    (ALIGN_LEFT, 'Left'),
    (ALIGN_RIGHT, 'Right'),
]


class DocumentTemplate(SoftDeleteModel):
    """
    A reusable template for generating personalized documents (certificates,
    offer letters, ID cards, marksheets, ...). The background is an uploaded
    image (PNG/JPG); any number of text fields can be placed at arbitrary
    coordinates and mapped from CSV columns.

    Generalizes the legacy ``certificates.CertificateTemplate`` (single fixed
    "name" field) into a multi-field template. Built alongside the certificate
    model; certificate data is copied here by a data migration.
    """

    OUTPUT_FORMAT_IMAGE = 'image'
    OUTPUT_FORMAT_PDF = 'pdf'
    OUTPUT_FORMAT_CHOICES = [
        (OUTPUT_FORMAT_IMAGE, 'Image (PNG)'),
        (OUTPUT_FORMAT_PDF, 'PDF'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='document_templates',
    )
    name = models.CharField(max_length=150)
    template_file = models.FileField(
        upload_to='document_templates/',
        help_text="Upload the background template as PNG or JPG.",
    )
    output_format = models.CharField(
        max_length=10, choices=OUTPUT_FORMAT_CHOICES, default=OUTPUT_FORMAT_PDF,
        help_text="File format produced for each recipient.",
    )

    # Typography mode. When use_global_typography is True every text field is
    # rendered with the template-level font/size/color/alignment below, and each
    # field's own typography is ignored. When False (default) each field uses
    # its own settings, preserving the original per-field behavior.
    use_global_typography = models.BooleanField(
        default=False,
        help_text="Apply one font, size, color and alignment to every text field.",
    )
    global_font_file = models.FileField(
        upload_to='document_fonts/', null=True, blank=True,
        help_text="Custom TTF applied to all fields when global typography is on.",
    )
    global_font_size = models.IntegerField(default=48, help_text="Font size (px) for all fields in global mode.")
    global_font_color = models.CharField(max_length=50, default="#000000", help_text="Font color for all fields in global mode.")
    global_alignment = models.CharField(
        max_length=10, choices=ALIGNMENT_CHOICES, default=ALIGN_CENTER,
        help_text="Alignment for all fields in global mode.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class DocumentTextField(models.Model):
    """
    A single text field rendered onto a ``DocumentTemplate``. Reads its value
    from a CSV column (``field_name``), positioned/aligned between
    ``x_start``/``x_end`` at ``y_coordinate``.
    """

    ALIGN_CENTER = 'center'
    ALIGN_LEFT = 'left'
    ALIGN_RIGHT = 'right'
    ALIGNMENT_CHOICES = [
        (ALIGN_CENTER, 'Center'),
        (ALIGN_LEFT, 'Left'),
        (ALIGN_RIGHT, 'Right'),
    ]

    template = models.ForeignKey(
        DocumentTemplate, on_delete=models.CASCADE, related_name='text_fields',
    )
    field_name = models.SlugField(
        max_length=100,
        help_text="CSV column header this field reads from (e.g. 'name', 'course_title').",
    )
    label = models.CharField(
        max_length=100, help_text="Human-readable label shown in the UI.",
    )
    x_start = models.IntegerField(help_text="Left boundary X coordinate (pixels).")
    x_end = models.IntegerField(help_text="Right boundary X coordinate (pixels).")
    y_coordinate = models.IntegerField(help_text="Y coordinate for the text baseline (pixels).")
    font_size = models.IntegerField(default=48, help_text="Font size in pixels.")
    font_color = models.CharField(
        max_length=50, default="#000000",
        help_text="Font color in hex (e.g. #000000) or rgb(0,0,0).",
    )
    font_file = models.FileField(
        upload_to='document_fonts/', null=True, blank=True,
        help_text="Optional custom TTF font. Falls back to the default if blank.",
    )
    alignment = models.CharField(
        max_length=10, choices=ALIGNMENT_CHOICES, default=ALIGN_CENTER,
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.template.name} — {self.label}"


class Signature(SoftDeleteModel):
    """
    A user-owned, reusable signature. The user uploads a photo/scan; a Celery
    task runs the background-removal pipeline and stores a clean transparent
    PNG in ``processed_image``. Soft-deleted so historical campaigns that
    referenced a signature by name keep a valid record.

    Per the platform's CSV-driven flow, a ``DocumentSignatureField`` resolves a
    recipient's signature by matching this model's ``name`` against a CSV cell.
    """

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    INK_PRESERVE = "preserve"
    INK_MONO = "mono"
    INK_MODE_CHOICES = [
        (INK_PRESERVE, "Preserve original ink color"),
        (INK_MONO, "Single color"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="signatures",
    )
    name = models.CharField(
        max_length=150,
        help_text="Label used to reference this signature from a CSV column.",
    )
    original_image = models.ImageField(
        upload_to=signature_original_upload_to,
        storage=encrypted_media_storage,
        help_text="The uploaded signature photo or scan.",
    )
    processed_image = models.ImageField(
        upload_to=signature_processed_upload_to,
        storage=encrypted_media_storage, null=True, blank=True,
        help_text="Background-removed transparent PNG (generated automatically).",
    )
    ink_color_mode = models.CharField(
        max_length=10, choices=INK_MODE_CHOICES, default=INK_PRESERVE,
        help_text="Keep the pen's original color, or recolor the strokes.",
    )
    ink_color = models.CharField(
        max_length=50, default="#000000",
        help_text="Stroke color used when 'Single color' mode is selected.",
    )
    processing_status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    confidence_score = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_active_signature_name_per_user",
            ),
        ]

    def __str__(self):
        return self.name

    def restore(self):
        # The partial unique constraint only covers active rows, so restoring a
        # signature whose name was reused by a live record would raise a raw
        # IntegrityError. Guard with a clear error instead (B-045).
        clash = type(self).active.filter(
            user=self.user, name=self.name,
        ).exclude(pk=self.pk).exists()
        if clash:
            raise ValidationError(
                f"Cannot restore signature '{self.name}': an active signature "
                f"with that name already exists."
            )
        super().restore()

    @property
    def is_ready(self):
        return self.processing_status == self.STATUS_READY and bool(self.processed_image)


class DocumentSignatureField(models.Model):
    """
    Placement of a per-recipient signature on a ``DocumentTemplate``. The
    rendered image is resolved at generation time from the CSV column named by
    ``source_column`` (its value matches a ``Signature.name``). The signature is
    scaled to fit ``max_height`` and the ``x_start``..``x_end`` box, preserving
    aspect ratio, and aligned within that box.

    Geometry mirrors ``DocumentTextField`` so the editor and preview reuse the
    same coordinate conventions.
    """

    template = models.ForeignKey(
        DocumentTemplate, on_delete=models.CASCADE, related_name="signature_fields",
    )
    label = models.CharField(
        max_length=100, help_text="Human-readable label shown in the UI.",
    )
    source_column = models.SlugField(
        max_length=100,
        help_text="CSV column whose value names the signature to place (e.g. 'signature').",
    )
    x_start = models.IntegerField(help_text="Left boundary X coordinate (pixels).")
    x_end = models.IntegerField(help_text="Right boundary X coordinate (pixels).")
    y_top = models.IntegerField(help_text="Top Y coordinate of the signature box (pixels).")
    max_height = models.IntegerField(
        default=120, help_text="Maximum signature height in pixels; scaled to fit.",
    )
    alignment = models.CharField(
        max_length=10, choices=ALIGNMENT_CHOICES, default=ALIGN_CENTER,
        help_text="Horizontal alignment within the box.",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.template.name} — {self.label}"


# Physical file cleanup is centralized in core.file_cleanup (single source of
# truth, transaction-safe). See that module for behavior.
from core.file_cleanup import register_file_cleanup

register_file_cleanup(DocumentTemplate, DocumentTextField, Signature)
