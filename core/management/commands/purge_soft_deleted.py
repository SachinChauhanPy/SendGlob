from django.core.management.base import BaseCommand

from core.tasks import purge_soft_deleted_records


class Command(BaseCommand):
    help = "Physically delete soft-deleted records past the retention window (runs the purge synchronously)."

    def handle(self, *args, **options):
        summary = purge_soft_deleted_records()
        self.stdout.write(self.style.SUCCESS(f"Purge complete: {summary}"))
