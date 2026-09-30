"""Process-local analysis repository used until durable persistence exists."""

from collections import OrderedDict
from threading import Lock
from uuid import UUID

from codebreakers.application.analysis import AnalysisJob


class InMemoryAnalysisRepository:
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
            self._jobs[job.id] = job
            while len(self._jobs) > self._capacity:
                self._jobs.popitem(last=False)

    def get(self, job_id: UUID) -> AnalysisJob | None:
        """Return a stored job, or ``None`` if unknown or evicted."""
        with self._lock:
            return self._jobs.get(job_id)
