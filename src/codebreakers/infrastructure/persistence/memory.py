"""Process-local analysis repository for tests and ephemeral local use."""

from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from threading import Lock
from uuid import UUID

from codebreakers.application.analysis import AnalysisJob, AnalysisRepository
from codebreakers.application.errors import ConcurrentAnalysisUpdateError
from codebreakers.application.messaging import AnalysisJobMessage, AnalysisOutbox


class InMemoryAnalysisRepository(AnalysisRepository, AnalysisOutbox):
    """Bounded, thread-safe store that evicts the oldest job when full.

    Source text and outbox entries live alongside jobs and are evicted with
    them.
    """

    def __init__(self, capacity: int = 128) -> None:
        if capacity < 1:
            msg = "capacity must be at least 1."
            raise ValueError(msg)
        self._capacity = capacity
        self._jobs: OrderedDict[UUID, AnalysisJob] = OrderedDict()
        self._inputs: dict[UUID, str] = {}
        self._outbox: OrderedDict[UUID, AnalysisJobMessage] = OrderedDict()
        self._lock = Lock()

    def add(self, job: AnalysisJob) -> None:
        """Store a job, evicting the oldest entry if capacity is exceeded."""
        with self._lock:
            self._insert(job)

    def enqueue(
        self, job: AnalysisJob, source_text: str, message: AnalysisJobMessage
    ) -> None:
        """Store a pending job with its source text and an outbox entry."""
        with self._lock:
            self._insert(job)
            self._inputs[job.id] = source_text
            self._outbox[job.id] = message

    def get_source_text(self, job_id: UUID) -> str | None:
        """Return retained source text for an unfinished job."""
        with self._lock:
            return self._inputs.get(job_id)

    def relay(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish pending outbox entries oldest first, stopping at a failure."""
        if limit < 1:
            msg = "limit must be positive."
            raise ValueError(msg)
        with self._lock:
            batch = list(self._outbox.items())[:limit]
        published = 0
        for job_id, message in batch:
            publish(message)
            with self._lock:
                self._outbox.pop(job_id, None)
            published += 1
        return published

    def recover(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish messages for unfinished jobs that have no outbox entry."""
        if limit < 1:
            msg = "limit must be positive."
            raise ValueError(msg)
        with self._lock:
            orphaned = [
                job_id
                for job_id, job in self._jobs.items()
                if not job.is_terminal and job_id not in self._outbox
            ][:limit]
        for job_id in orphaned:
            publish(AnalysisJobMessage(job_id=job_id))
        return len(orphaned)

    def _insert(self, job: AnalysisJob) -> None:
        if job.id in self._jobs:
            msg = f"Analysis '{job.id}' already exists."
            raise ValueError(msg)
        self._jobs[job.id] = job
        while len(self._jobs) > self._capacity:
            evicted, _ = self._jobs.popitem(last=False)
            self._forget(evicted)

    def _forget(self, job_id: UUID) -> None:
        self._inputs.pop(job_id, None)
        self._outbox.pop(job_id, None)

    def get(self, job_id: UUID) -> AnalysisJob | None:
        """Return a stored job, or ``None`` if unknown or evicted."""
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, offset: int, limit: int) -> tuple[AnalysisJob, ...]:
        """Return a page ordered by creation time, newest first."""
        if offset < 0 or limit < 1:
            msg = "offset must be non-negative and limit must be positive."
            raise ValueError(msg)
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda job: (job.created_at, str(job.id)),
                reverse=True,
            )
            return tuple(jobs[offset : offset + limit])

    def count(self) -> int:
        """Return the number of retained jobs."""
        with self._lock:
            return len(self._jobs)

    def update(self, job: AnalysisJob, expected_version: int) -> AnalysisJob:
        """Update a job if its persisted version still matches."""
        with self._lock:
            current = self._jobs.get(job.id)
            if current is None or current.version != expected_version:
                raise ConcurrentAnalysisUpdateError(
                    f"Analysis '{job.id}' was modified by another operation."
                )
            if job.version != expected_version + 1:
                msg = "Updated job version must increment the expected version by one."
                raise ValueError(msg)
            self._jobs[job.id] = job
            if job.is_terminal:
                self._inputs.pop(job.id, None)
            return job

    def delete_expired(self, before: datetime) -> int:
        """Delete jobs created before the retention cutoff."""
        with self._lock:
            expired = [
                job_id for job_id, job in self._jobs.items() if job.created_at < before
            ]
            for job_id in expired:
                del self._jobs[job_id]
                self._forget(job_id)
            return len(expired)
