from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """Read-oriented raw access; day-to-day handling happens in the ops console
    Messages section (which audits status changes)."""

    list_display = ['created_at', 'name', 'email', 'subject', 'status', 'handled_by']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'email', 'subject', 'message']
    readonly_fields = ['name', 'email', 'subject', 'message', 'user',
                       'ip_address', 'user_agent', 'created_at']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        # Messages arrive only through the public contact form.
        return False
