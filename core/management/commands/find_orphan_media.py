"""
Reconcile ``MEDIA_ROOT`` against the database and report (optionally delete)
orphaned files: files on disk that no database row references.

The centralized cleanup signals (``core.file_cleanup``) keep storage in sync going
forward, but files orphaned before the signals existed, or by any bulk ``update()``
that bypasses ``pre_save``, are invisible to them. This command is the
reconciliation safety net (see B-042 and ``docs/file_storage_cleanup_plan.md``).

Referenced files are collected from every ``FileField``/``ImageField`` on every
installed model using each model's *base* manager, so intentionally retained
soft-deleted files still count as referenced and are never treated as orphans.

Defaults to a dry run. Pass ``--delete`` to remove what it finds, and
``--older-than DAYS`` to skip recently written files (avoids deleting a file
mid-upload).
"""
import os
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone


def _file_fields(model):
    """Return the ``FileField``/``ImageField`` instances declared on ``model``."""
    return [
        field for field in model._meta.fields
        if isinstance(field, (models.FileField, models.ImageField))
    ]


def referenced_file_names():
    """Build the set of storage names referenced by any row in the database.

    Uses ``_base_manager`` (unfiltered) so soft-deleted-but-retained files are
    still counted as referenced and never reported as orphans.
    """
    referenced = set()
    for model in apps.get_models():
        field_names = [f.name for f in _file_fields(model)]
        if not field_names:
            continue
        for values in model._base_manager.values_list(*field_names):
            # values is a tuple aligned with field_names (a scalar-wrapped tuple
            # when there is a single field).
            for name in values:
                if name:
                    referenced.add(name)
    return referenced


class Command(BaseCommand):
    help = (
        "Find media files on disk that no database record references. "
        "Dry run by default; use --delete to remove them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help="Delete the orphaned files instead of only reporting them.",
        )
        parser.add_argument(
            '--older-than', type=int, default=0, metavar='DAYS',
            help="Only consider files last modified more than DAYS days ago.",
        )

    def handle(self, *args, **options):
        do_delete = options['delete']
        older_than_days = options['older_than']

        media_root = str(settings.MEDIA_ROOT)
        if not os.path.isdir(media_root):
            self.stdout.write(self.style.WARNING(f"MEDIA_ROOT does not exist: {media_root}"))
            return

        referenced = referenced_file_names()
        cutoff = timezone.now() - timedelta(days=older_than_days) if older_than_days else None

        scanned = 0
        orphans = []
        skipped_recent = 0

        for dirpath, _dirnames, filenames in os.walk(media_root):
            for filename in filenames:
                abs_path = os.path.join(dirpath, filename)
                # Storage names are relative to MEDIA_ROOT with forward slashes.
                rel_name = os.path.relpath(abs_path, media_root).replace(os.sep, '/')
                scanned += 1

                if rel_name in referenced:
                    continue

                if cutoff is not None:
                    mtime = timezone.datetime.fromtimestamp(
                        os.path.getmtime(abs_path), tz=timezone.get_current_timezone()
                    )
                    if mtime > cutoff:
                        skipped_recent += 1
                        continue

                orphans.append(rel_name)

        for rel_name in orphans:
            if do_delete:
                try:
                    default_storage.delete(rel_name)
                    self.stdout.write(self.style.SUCCESS(f"Deleted orphan: {rel_name}"))
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"Failed to delete {rel_name}: {exc}"))
            else:
                self.stdout.write(f"Orphan: {rel_name}")

        action = "Deleted" if do_delete else "Found"
        summary = (
            f"{action} {len(orphans)} orphan file(s); "
            f"scanned {scanned}, referenced {len(referenced)}"
        )
        if skipped_recent:
            summary += f", skipped {skipped_recent} recent (< {older_than_days}d)"
        if not do_delete and orphans:
            summary += ". Re-run with --delete to remove them."
        self.stdout.write(self.style.SUCCESS(summary))
