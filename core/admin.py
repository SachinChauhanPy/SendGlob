from django.contrib import admin

from core.models import PurgeLog


@admin.register(PurgeLog)
class PurgeLogAdmin(admin.ModelAdmin):
    """Read-only audit view of data-purge runs."""
    list_display = [
        'ran_at', 'retention_days', 'campaigns_purged', 'email_templates_purged',
        'document_templates_purged', 'signatures_purged', 'smtp_configs_purged',
        'total_purged',
    ]
    list_filter = ['ran_at']
    readonly_fields = [
        'ran_at', 'retention_days', 'campaigns_purged', 'email_templates_purged',
        'document_templates_purged', 'signatures_purged', 'smtp_configs_purged',
        'notes',
    ]
    ordering = ['-ran_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
