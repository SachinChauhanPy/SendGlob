from django.conf import settings
from django.db import models


class AdminAuditLog(models.Model):
    """Append-only record of every operator action taken through the console.

    Deliberately NOT a ``SoftDeleteModel``: this is an audit trail, so rows are
    never edited or removed (it is also excluded from the soft-delete purge).
    Phase A only writes ``view`` rows (impersonation, sensitive reads); the
    control actions in later phases reuse the same table via :func:`record`.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ops_actions',
    )
    action = models.CharField(max_length=50)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admin Audit Log'
        verbose_name_plural = 'Admin Audit Logs'
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['target_type', 'target_id']),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_id} {self.action}"

    @classmethod
    def record(cls, *, actor, action, target=None, reason='', detail=None,
               ip_address=None):
        """Single entry point for writing an audit row.

        Routing every write through here keeps the shape consistent and gives
        the control actions in later phases one place to call. ``target`` may be
        any model instance; its type name and pk are stored as strings so the
        log survives even if the target is later purged.
        """
        target_type = ''
        target_id = ''
        if target is not None:
            target_type = target._meta.model_name
            target_id = str(target.pk)
        return cls.objects.create(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason or '',
            detail=detail or {},
            ip_address=ip_address,
        )


class MetricSnapshot(models.Model):
    """One sampled value of one metric at one instant.

    Operational telemetry, not financial history: rows may be downsampled or
    pruned on a long retention (see ``opsconsole.tasks.prune_metric_snapshots``).
    Kept deliberately narrow so the collector is cheap and trend queries are a
    simple indexed scan by ``(metric, captured_at)``.
    """

    metric = models.CharField(max_length=60, db_index=True)
    value = models.FloatField()
    captured_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-captured_at']
        indexes = [
            models.Index(fields=['metric', 'captured_at']),
        ]

    def __str__(self):
        return f"{self.metric}={self.value} @ {self.captured_at:%Y-%m-%d %H:%M}"


class AlertEvent(models.Model):
    """A threshold breach worth an operator's attention.

    De-duplicated by the collector: while an unacknowledged event exists for the
    same ``(metric, level)`` no duplicate is created, so a sustained breach does
    not spam. Acknowledging clears the suppression.
    """

    WARN = 'warn'
    CRITICAL = 'critical'
    LEVEL_CHOICES = [(WARN, 'Warning'), (CRITICAL, 'Critical')]

    metric = models.CharField(max_length=60, db_index=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    message = models.CharField(max_length=255)
    value = models.FloatField()
    threshold = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='ops_acknowledged_alerts',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['metric', 'level', 'acknowledged_at']),
        ]

    def __str__(self):
        return f"[{self.level}] {self.metric} {self.value} (>{self.threshold})"

    @property
    def is_active(self):
        return self.acknowledged_at is None


class OperationalFlag(models.Model):
    """Runtime operational switch honored by the workers without a redeploy.

    Read on the hot path by the send and generation pipelines, so reads are
    cached briefly (see ``opsconsole.flags``). Writes go through the audited
    ``opsconsole.controls.set_flag`` so every toggle is attributable.

    Known keys (see ``opsconsole.flags``):
        global_send_paused  - kill switch: pause all outbound delivery.
        maintenance_mode    - block non-staff writes with a friendly page.
        send_throttle       - override inter-send delay (seconds) during incidents.
    """

    key = models.CharField(max_length=50, unique=True)
    enabled = models.BooleanField(default=False)
    value = models.CharField(max_length=100, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        state = 'on' if self.enabled else 'off'
        return f"{self.key}={state}" + (f" ({self.value})" if self.value else '')
