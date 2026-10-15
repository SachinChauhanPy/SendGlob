from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from accounts.models import CustomUser, SMTPConfig

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['email', 'username', 'is_staff', 'is_active']
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ()}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {
            'classes': ('collapse',),
            'fields': ('email',),
        }),
    )

class SMTPConfigAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'smtp_username', 'smtp_host', 'smtp_port', 'is_active', 'created_at']
    list_filter = ['is_active', 'use_tls', 'smtp_host']
    search_fields = ['name', 'smtp_username', 'user__email']
    ordering = ['-created_at']

admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(SMTPConfig, SMTPConfigAdmin)
