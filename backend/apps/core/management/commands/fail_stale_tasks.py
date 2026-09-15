"""Mark tasks orphaned by a server restart as FAILED.

Run by entrypoint.sh on boot: any Task left PENDING or PROCESSING by a
previous process can never finish (its ThreadPoolExecutor is gone), so it is
failed with a clear, user-facing error message.
"""
from django.utils import timezone

from django.core.management.base import BaseCommand

from apps.core.models import Task


class Command(BaseCommand):
    """fail_stale_tasks — fail tasks interrupted by a server restart."""

    help = (
        "Mark every Task still PENDING or PROCESSING as FAILED with error "
        "'Interrupted by server restart'. Safe to run repeatedly."
    )

    def handle(self, *args, **options):
        """Fail all stale tasks and print how many were affected."""
        # updated_at is bumped explicitly: QuerySet.update() bypasses the
        # field's auto_now, and polling clients rely on it to notice the
        # status change.
        count = Task.objects.filter(
            status__in=[Task.STATUS_PENDING, Task.STATUS_PROCESSING]
        ).update(
            status=Task.STATUS_FAILED,
            error="Interrupted by server restart",
            progress=100.0,
            updated_at=timezone.now(),
        )
        self.stdout.write(f"Failed {count} stale task(s).")
