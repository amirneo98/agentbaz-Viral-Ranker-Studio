"""Tests for the task API, the runner lifecycle and the stale-task command.

The lifecycle cases run against real runner threads, so they use
TransactionTestCase: worker threads own their database connections and can
only see rows that were committed, which TestCase's per-test transaction
would hide.
"""
import io
import threading
import time
import uuid

from django.core.management import call_command
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from rest_framework.test import APIClient, APITestCase

from apps.core import runner
from apps.core.models import Task

TERMINAL_STATUSES = (Task.STATUS_SUCCESS, Task.STATUS_FAILED)

TASK_FIELDS = {
    "id",
    "task_type",
    "status",
    "progress",
    "error",
    "result",
    "created_at",
    "updated_at",
}


def wait_for_task(client, task_id, predicate, timeout=10.0, interval=0.01):
    """Poll the task detail endpoint until ``predicate(response)`` holds.

    Raises AssertionError with the last observed payload on timeout, so a
    wedged worker produces a readable failure instead of a silent hang.
    """
    url = reverse("task-detail", args=[task_id])
    deadline = time.monotonic() + timeout
    response = None
    while time.monotonic() < deadline:
        response = client.get(url, format="json")
        if response.status_code == 200 and predicate(response):
            return response
        time.sleep(interval)
    last = getattr(response, "data", None) if response is not None else None
    raise AssertionError(
        f"task {task_id} did not reach the expected state within "
        f"{timeout:.0f}s; last response: {last!r}"
    )


class HealthTests(APITestCase):
    """GET /api/health/ — docker-compose liveness probe."""

    def test_returns_ok(self):
        response = self.client.get(reverse("health"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok"})


class TaskDetailTests(APITestCase):
    """GET /api/tasks/<task_id>/"""

    def test_unknown_task_returns_404(self):
        response = self.client.get(
            reverse("task-detail", args=[uuid.uuid4()]), format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_pending_task_payload_shape(self):
        task = Task.objects.create(task_type=Task.TYPE_FETCH)
        response = self.client.get(reverse("task-detail", args=[task.id]), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), TASK_FIELDS)
        self.assertEqual(response.data["id"], str(task.id))
        self.assertEqual(response.data["task_type"], Task.TYPE_FETCH)
        self.assertEqual(response.data["status"], Task.STATUS_PENDING)
        self.assertEqual(response.data["progress"], 0.0)
        self.assertEqual(response.data["error"], "")
        self.assertIsNone(response.data["result"])


class TaskLifecycleTests(TransactionTestCase):
    """Full task lifecycle through the real ThreadPoolExecutor.

    TransactionTestCase (not TestCase) is required: ``runner`` executes the
    callable on a worker thread with its own DB connection, which cannot
    see rows created inside the test's rolled-back transaction.
    """

    client_class = APIClient

    def _drain(self, task_id):
        """Ensure the worker for *task_id* reached a terminal state.

        Keeps a failed assertion from leaking a still-running job into the
        next test (and from racing the teardown flush).
        """
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            task = Task.objects.filter(pk=task_id).first()
            if task and task.status in TERMINAL_STATUSES:
                return
            time.sleep(0.01)
        raise AssertionError(f"task {task_id} never reached a terminal state")

    def test_success_lifecycle_reports_progress_then_result(self):
        release = threading.Event()

        def fake_job(task):
            runner.set_progress(task.id, 42.0)
            # Block until the test has observed the intermediate progress,
            # making the PROCESSING/42.0 state deterministic.
            release.wait(timeout=10)
            return {"answer": 42, "video_url": "/media/videos/answer.mp4"}

        task = runner.submit(Task.TYPE_FETCH, fake_job)
        try:
            seen = wait_for_task(
                self.client,
                task.id,
                lambda r: r.data["status"] == Task.STATUS_PROCESSING
                and r.data["progress"] == 42.0,
            )
            self.assertEqual(seen.data["error"], "")
        finally:
            release.set()
            self._drain(task.id)

        done = wait_for_task(
            self.client, task.id, lambda r: r.data["status"] == Task.STATUS_SUCCESS
        )
        self.assertEqual(done.data["task_type"], Task.TYPE_FETCH)
        self.assertEqual(done.data["progress"], 100.0)
        self.assertEqual(
            done.data["result"],
            {"answer": 42, "video_url": "/media/videos/answer.mp4"},
        )

    def test_failing_job_marks_task_failed_with_error(self):
        def broken_job(task):
            raise ValueError("boom")

        task = runner.submit(Task.TYPE_RENDER, broken_job)
        try:
            response = wait_for_task(
                self.client, task.id, lambda r: r.data["status"] == Task.STATUS_FAILED
            )
        finally:
            self._drain(task.id)
        self.assertIn("boom", response.data["error"])
        self.assertIsNone(response.data["result"])


class FailStaleTasksTests(APITestCase):
    """The ``fail_stale_tasks`` management command run by entrypoint.sh."""

    def _make(self, **overrides):
        return Task.objects.create(task_type=Task.TYPE_FETCH, **overrides)

    def test_fails_pending_and_processing_only(self):
        pending = self._make()
        processing = self._make(status=Task.STATUS_PROCESSING, progress=55.0)
        succeeded = self._make(
            status=Task.STATUS_SUCCESS, progress=100.0, result={"ok": True}
        )
        already_failed = self._make(status=Task.STATUS_FAILED, error="old error")

        original_updated = processing.updated_at
        # QuerySet.update() skips auto_now; without an explicit bump the
        # stale task's updated_at would never change, hiding the status
        # flip from polling clients.
        self.assertLess(processing.updated_at, timezone.now())
        time.sleep(0.01)

        stdout = io.StringIO()
        call_command("fail_stale_tasks", stdout=stdout)

        self.assertIn("2", stdout.getvalue())
        for stale in (pending, processing):
            stale.refresh_from_db()
            self.assertEqual(stale.status, Task.STATUS_FAILED)
            self.assertEqual(stale.error, "Interrupted by server restart")
        processing.refresh_from_db()
        self.assertGreater(processing.updated_at, original_updated)
        succeeded.refresh_from_db()
        self.assertEqual(succeeded.status, Task.STATUS_SUCCESS)
        self.assertEqual(succeeded.result, {"ok": True})
        already_failed.refresh_from_db()
        self.assertEqual(already_failed.status, Task.STATUS_FAILED)
        self.assertEqual(already_failed.error, "old error")

    def test_is_idempotent(self):
        self._make(status=Task.STATUS_PROCESSING, progress=10.0)
        call_command("fail_stale_tasks")
        stdout = io.StringIO()
        call_command("fail_stale_tasks", stdout=stdout)
        self.assertIn("0", stdout.getvalue())
        self.assertEqual(
            Task.objects.filter(status=Task.STATUS_PROCESSING).count(), 0
        )
