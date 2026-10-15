from django.db import models
from django.utils import timezone

class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        # Perform soft delete in bulk queries
        return super().update(deleted_at=timezone.now())

    def hard_delete(self):
        # Perform physical delete in bulk queries
        return super().delete()

class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        # Exclude soft-deleted records by default
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)

class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()  # Default manager (unfiltered to keep FK lookups intact)
    active = SoftDeleteManager()  # Filters out soft-deleted records

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        # Soft delete individual record
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])

    def restore(self):
        # Restore soft-deleted record
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])

    def hard_delete(self, using=None, keep_parents=False):
        # Hard delete individual record
        super().delete(using=using, keep_parents=keep_parents)


class PurgeLog(models.Model):
    """
    Lightweight audit trail for data-purge runs. One row per
    ``purge_soft_deleted_records`` execution, recording how many records of each
    type were physically deleted. Kept indefinitely so operators can see purge
    history in the admin (historical accuracy applies to the audit log too).
    """
    ran_at = models.DateTimeField(auto_now_add=True)
    retention_days = models.IntegerField(default=0)
    campaigns_purged = models.IntegerField(default=0)
    email_templates_purged = models.IntegerField(default=0)
    document_templates_purged = models.IntegerField(default=0)
    signatures_purged = models.IntegerField(default=0)
    smtp_configs_purged = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-ran_at']
        verbose_name = 'Purge Log'
        verbose_name_plural = 'Purge Logs'

    @property
    def total_purged(self):
        return (
            self.campaigns_purged
            + self.email_templates_purged
            + self.document_templates_purged
            + self.signatures_purged
            + self.smtp_configs_purged
        )

    def __str__(self):
        return f"Purge {self.ran_at:%Y-%m-%d %H:%M}: {self.total_purged} record(s)"
