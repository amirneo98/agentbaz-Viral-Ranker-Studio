"""In-process asynchronous task runner.

Downloads and renders are submitted to a module-level ThreadPoolExecutor so
HTTP requests return immediately with a task id. Task state is persisted in
the database, which lets any request thread serve polling requests at
``GET /api/tasks/<task_id>/``.

Public API used by the pipelines:

    task = runner.submit(Task.TYPE_FETCH, run_fetch, url)
    runner.set_progress(task.id, 42.0)

The callable receives the freshly created Task instance as its first
argument and must return a JSON-serializable dict that gets stored on the
task as ``result`` when it finishes successfully.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from apps.core.models import Task

logger = logging.getLogger(__name__)

# One download and one render may run side by side; more than that would
# thrash the single GPU / disk of the local workstation.
MAX_WORKERS = 2

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="vrs")


def submit(task_type, fn, *args, **kwargs):
    """Create a PENDING Task row and schedule ``fn(task, *args, **kwargs)``."""
    task = Task.objects.create(task_type=task_type)
    schedule_existing(task, fn, *args, **kwargs)
    return task


def schedule_existing(task, fn, *args, **kwargs):
    """Schedule work for a Task row that already exists (e.g. RenderJob FK)."""
    _executor.submit(_run, task.pk, fn, args, kwargs)


def set_progress(task_id, progress):
    """Best-effort progress update (clamped to 0-100). Never raises."""
    try:
        Task.objects.filter(pk=task_id, status=Task.STATUS_PROCESSING).update(
            progress=min(100.0, max(0.0, float(progress)))
        )
    except Exception:  # noqa: BLE001 - progress must never kill a worker
        logger.exception("Failed to update progress for task %s", task_id)


def _run(task_id, fn, args, kwargs):
    close_old_connections()
    task = Task.objects.get(pk=task_id)
    try:
        _transition(task, status=Task.STATUS_PROCESSING, progress=0.0)
        result = fn(task, *args, **kwargs) or {}
        # The callable may have failed the task itself; respect that.
        task.refresh_from_db()
        if task.status == Task.STATUS_PROCESSING:
            _transition(
                task, status=Task.STATUS_SUCCESS, progress=100.0, result=result
            )
    except Exception as exc:  # noqa: BLE001 - worker boundary
        logger.exception("Task %s failed", task_id)
        _transition(task, status=Task.STATUS_FAILED, error=str(exc) or repr(exc))
    finally:
        close_old_connections()


def _transition(task, **fields):
    for name, value in fields.items():
        setattr(task, name, value)
    task.save(update_fields=list(fields) + ["updated_at"])
