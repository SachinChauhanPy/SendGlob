from django.contrib import admin

from .models import AdminAuditLog, AlertEvent, MetricSnapshot, OperationalFlag


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    """Read-only inspection of the audit trail. Rows are never edited or deleted
    from the admin: the log is append-only (historical accuracy applies here too).
    """
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id', 'ip_address')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('actor__email', 'action', 'target_id', 'reason')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'metric', 'level', 'value', 'threshold', 'acknowledged_at')
    list_filter = ('level', 'metric', 'acknowledged_at')
    search_fields = ('metric', 'message')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ('captured_at', 'metric', 'value')
    list_filter = ('metric',)
    date_hierarchy = 'captured_at'
    ordering = ('-captured_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OperationalFlag)
class OperationalFlagAdmin(admin.ModelAdmin):
    list_display = ('key', 'enabled', 'value', 'updated_by', 'updated_at')
    list_filter = ('enabled',)
    search_fields = ('key',)
