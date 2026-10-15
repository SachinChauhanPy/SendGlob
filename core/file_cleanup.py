"""
Single source of truth for keeping physical files in sync with the database.

Register a model with :func:`register_file_cleanup`. For each registered model:

* on update, a ``FileField``/``ImageField`` whose stored file is replaced has its
  old file removed from storage;
* on physical delete, every file field is removed from storage.

Removals are deferred with ``transaction.on_commit`` so a file is deleted only
after the database change is durable. If the surrounding transaction rolls back,
the file is left untouched, so a record never ends up pointing at a missing file.

Soft deletes do not fire ``post_delete`` (``SoftDeleteModel.delete`` only sets
``deleted_at``), so files for soft-deleted records are retained until the record
is physically purged. That is intentional and preserves historical accuracy;
physical reclamation happens in ``core.tasks.purge_soft_deleted_records``.

This module is the only place file-cleanup behavior is defined. App models call
``register_file_cleanup(...)`` instead of duplicating signal handlers.
"""
import logging

from django.db import models, transaction
from django.db.models.signals import post_delete, pre_save

logger = logging.getLogger(__name__)


def _file_fields(model):
    """Return the ``FileField``/``ImageField`` instances declared on ``model``."""
    return [
        field for field in model._meta.fields
        if isinstance(field, (models.FileField, models.ImageField))
    ]


def _schedule_delete(file_field, sender, pk, field_name, reason):
    """Remove ``file_field`` from storage once the current transaction commits.

    Outside an atomic block ``on_commit`` runs the callback immediately, so this
    also works in management commands and autocommit contexts. Storage errors are
    logged rather than raised: a failed cleanup must not break the request that
    triggered it.
    """
    def _remove():
        try:
            file_field.delete(save=False)
            logger.info(
                "Deleted physical file (%s) for %s pk=%s field=%s",
                reason, sender.__name__, pk, field_name,
            )
        except Exception as exc:
            logger.error(
                "Error deleting file (%s) for %s pk=%s field=%s: %s",
                reason, sender.__name__, pk, field_name, exc,
            )

    transaction.on_commit(_remove)


def _cleanup_pre_save(sender, instance, raw=False, update_fields=None, **kwargs):
    """Delete the previous file when a file field is replaced on an existing row.

    Skipped for new rows, fixture loads (``raw``), and saves whose
    ``update_fields`` touch no file field (the common case for status and
    ``deleted_at`` updates), avoiding an extra query on hot save paths.
    """
    if raw or not instance.pk:
        return

    field_names = [field.name for field in _file_fields(sender)]
    if not field_names:
        return
    if update_fields is not None and not (set(update_fields) & set(field_names)):
        return

    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    for name in field_names:
        old_file = getattr(old_instance, name)
        new_file = getattr(instance, name)
        if old_file and old_file.name and old_file.name != new_file.name:
            _schedule_delete(old_file, sender, instance.pk, name, "replaced")


def _cleanup_post_delete(sender, instance, **kwargs):
    """Delete every file field when a row is physically removed."""
    for name in [field.name for field in _file_fields(sender)]:
        file_field = getattr(instance, name)
        if file_field and file_field.name:
            _schedule_delete(file_field, sender, instance.pk, name, "record deleted")


def register_file_cleanup(*models_to_register):
    """Connect transaction-safe file cleanup for each model with file fields.

    ``dispatch_uid`` makes registration idempotent, so repeated imports do not
    attach duplicate handlers.
    """
    for model in models_to_register:
        if not _file_fields(model):
            continue
        label = model._meta.label
        pre_save.connect(
            _cleanup_pre_save, sender=model,
            dispatch_uid=f"file_cleanup_pre_save_{label}",
        )
        post_delete.connect(
            _cleanup_post_delete, sender=model,
            dispatch_uid=f"file_cleanup_post_delete_{label}",
        )
