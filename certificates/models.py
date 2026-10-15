from django.db import models
from django.conf import settings
from core.models import SoftDeleteModel

class CertificateTemplate(SoftDeleteModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='certificate_templates')
    name = models.CharField(max_length=150)
    image = models.ImageField(upload_to='certificate_templates/', help_text="Upload the certificate background image (PNG or JPG)")
    x_start = models.IntegerField(help_text="Starting X coordinate for name alignment")
    x_end = models.IntegerField(help_text="Ending X coordinate for name alignment")
    y_coordinate = models.IntegerField(help_text="Y coordinate for the text baseline")
    font_size = models.IntegerField(default=48, help_text="Font size in pixels")
    font_color = models.CharField(max_length=50, default="#000000", help_text="Font color in hex (e.g. #000000) or RGB (e.g. rgb(0,0,0))")
    font_file = models.FileField(upload_to='certificate_fonts/', null=True, blank=True, help_text="Upload a custom TTF font file. If blank, default font will be used.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


# Physical file cleanup is centralized in core.file_cleanup (single source of
# truth, transaction-safe). See that module for behavior.
from core.file_cleanup import register_file_cleanup

register_file_cleanup(CertificateTemplate)
