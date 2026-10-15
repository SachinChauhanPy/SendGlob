from django.contrib import admin
from emails.models import EmailTemplate, EmailCampaign, Recipient, CampaignAttachment

class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'subject', 'created_at']
    search_fields = ['name', 'subject', 'user__email']
    ordering = ['-created_at']

class CampaignAttachmentInline(admin.TabularInline):
    model = CampaignAttachment
    extra = 1

class RecipientInline(admin.TabularInline):
    model = Recipient
    fields = ['name', 'email', 'status', 'sent_at', 'opened_at', 'retry_count']
    readonly_fields = ['sent_at', 'opened_at', 'retry_count']
    extra = 0

class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'smtp_config', 'status', 'schedule_type', 'is_recurring', 'next_run_at', 'total_emails', 'sent_emails', 'failed_emails', 'is_document_campaign', 'created_at']
    list_filter = ['status', 'schedule_type', 'is_recurring', 'is_document_campaign', 'created_at']
    search_fields = ['name', 'user__email']
    readonly_fields = ['next_run_at', 'reminder_sent_at']
    inlines = [CampaignAttachmentInline, RecipientInline]
    ordering = ['-created_at']

class RecipientAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'campaign', 'status', 'sent_at', 'opened_at', 'retry_count']
    list_filter = ['status', 'sent_at', 'opened_at']
    search_fields = ['name', 'email', 'campaign__name']
    ordering = ['-sent_at']

admin.site.register(EmailTemplate, EmailTemplateAdmin)
admin.site.register(EmailCampaign, EmailCampaignAdmin)
admin.site.register(Recipient, RecipientAdmin)
