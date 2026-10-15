from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from documents.models import (
    DocumentTemplate, DocumentTextField, DocumentSignatureField, Signature,
)


class DocumentTextFieldInline(admin.TabularInline):
    model = DocumentTextField
    extra = 1
    fields = ['field_name', 'label', 'x_start', 'x_end',
              'y_coordinate', 'font_size', 'font_color', 'alignment', 'font_file']


class DocumentSignatureFieldInline(admin.TabularInline):
    model = DocumentSignatureField
    extra = 0
    fields = ['label', 'source_column', 'x_start', 'x_end', 'y_top', 'max_height', 'alignment']


class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'output_format', 'use_global_typography', 'created_at']
    list_filter = ['output_format', 'use_global_typography', 'created_at']
    search_fields = ['name', 'user__email']
    ordering = ['-created_at']
    inlines = [DocumentTextFieldInline, DocumentSignatureFieldInline]


class SignatureAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'processing_status', 'ink_color_mode', 'confidence_score', 'created_at']
    list_filter = ['processing_status', 'ink_color_mode', 'created_at']
    search_fields = ['name', 'user__email']
    ordering = ['-created_at']
    # B-053: the image fields use EncryptedFileSystemStorage and must never be
    # rendered via field.url (the public /media/ path serves ciphertext). The
    # raw FileFields are excluded and replaced with links to the authenticated
    # serving view (documents:signature_image).
    exclude = ['original_image', 'processed_image']
    readonly_fields = [
        'processing_status', 'confidence_score', 'error_message',
        'original_image_link', 'processed_image_link',
    ]

    def has_add_permission(self, request):
        # Signatures are created through the app's upload pipeline (background
        # removal task); adding one here would bypass processing and encryption.
        return False

    @admin.display(description='Original image')
    def original_image_link(self, obj):
        return self._image_link(obj, 'original')

    @admin.display(description='Processed image')
    def processed_image_link(self, obj):
        return self._image_link(obj, 'processed')

    def _image_link(self, obj, variant):
        field = obj.original_image if variant == 'original' else obj.processed_image
        if not obj.pk or not field:
            return '-'
        url = reverse('documents:signature_image', kwargs={'pk': obj.pk, 'variant': variant})
        return format_html('<a href="{}" target="_blank" rel="noopener">View {} image</a>', url, variant)


admin.site.register(DocumentTemplate, DocumentTemplateAdmin)
admin.site.register(Signature, SignatureAdmin)
