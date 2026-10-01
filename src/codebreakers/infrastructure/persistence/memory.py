"""Process-local analysis repository used until durable persistence exists."""

from collections import OrderedDict
from datetime import datetime
from threading import Lock
from uuid import UUID

from codebreakers.application.analysis import AnalysisJob, AnalysisRepository
from codebreakers.application.errors import ConcurrentAnalysisUpdateError


class InMemoryAnalysisRepository(AnalysisRepository):
    """Bounded, thread-safe store that evicts the oldest job when full."""

    def __init__(self, capacity: int = 128) -> None:
        if capacity < 1:
            msg = "capacity must be at least 1."
            raise ValueError(msg)
        self._capacity = capacity
        self._jobs: OrderedDict[UUID, AnalysisJob] = OrderedDict()
        self._lock = Lock()

    def add(self, job: AnalysisJob) -> None:
        """Store a job, evicting the oldest entry if capacity is exceeded."""
        with self._lock:
            if job.id in self._jobs:
                msg = f"Analysis '{job.id}' already exists."
                raise ValueError(msg)
            self._jobs[job.id] = job
            while len(self._jobs) > self._capacity:
                self._jobs.popitem(last=False)

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
            return job

    def delete_expired(self, before: datetime) -> int:
        """Delete jobs created before the retention cutoff."""
        with self._lock:
            expired = [
                job_id for job_id, job in self._jobs.items() if job.created_at < before
            ]
            for job_id in expired:
                del self._jobs[job_id]
            return len(expired)
