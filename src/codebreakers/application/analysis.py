"""Application service and ports for cryptanalysis jobs."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from codebreakers.application.errors import (
    AnalysisNotFoundError,
    UnsupportedAnalyzerError,
    UnsupportedLanguageError,
)
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.domain.cryptanalysis.analyzers import VigenereAnalysisResult
from codebreakers.domain.cryptanalysis.models import (
    AnalysisResult,
    Analyzer,
    FrequencyAnalysisReport,
    LanguageModel,
)

type AnalysisOutcome = AnalysisResult | FrequencyAnalysisReport | VigenereAnalysisResult
type AnalyzerFactory = Callable[[LanguageModel], Analyzer[AnalysisOutcome]]


class AnalysisStatus(StrEnum):
    """Lifecycle states of an analysis job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: Mapping[AnalysisStatus, frozenset[AnalysisStatus]] = {
    AnalysisStatus.PENDING: frozenset(
        {AnalysisStatus.RUNNING, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}
    ),
    AnalysisStatus.RUNNING: frozenset(
        {AnalysisStatus.SUCCEEDED, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}
    ),
    AnalysisStatus.SUCCEEDED: frozenset(),
    AnalysisStatus.FAILED: frozenset(),
    AnalysisStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES: frozenset[AnalysisStatus] = frozenset(
    {AnalysisStatus.SUCCEEDED, AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class AnalysisJob:
    """An analysis request together with its lifecycle state and outcome."""

    id: UUID
    analyzer: str
    language: str
    status: AnalysisStatus
    created_at: datetime
    completed_at: datetime | None
    result: AnalysisOutcome | None
    updated_at: datetime | None = None
    error_code: str | None = None
    version: int = 1
    parameters: Mapping[str, str] = field(default_factory=dict)
    attempts: int = 0
    lease_expires_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Return whether the job has reached a final state."""
        return self.status in TERMINAL_STATUSES

    def is_claimable(self, at: datetime) -> bool:
        """Return whether a worker may start, or take over, this job at ``at``."""
        if self.status is AnalysisStatus.PENDING:
            return True
        return self.status is AnalysisStatus.RUNNING and (
            self.lease_expires_at is None or self.lease_expires_at <= at
        )

    def claim(self, at: datetime, lease: timedelta) -> "AnalysisJob":
        """Return the job running under a new lease held until ``at + lease``.

        A running job can be reclaimed once its lease lapses, which recovers
        work abandoned by a worker that stopped mid-analysis.
        """
        if not self.is_claimable(at):
            msg = f"Analysis in status {self.status} is not claimable at {at}."
            raise ValueError(msg)
        return replace(
            self,
            status=AnalysisStatus.RUNNING,
            updated_at=at,
            attempts=self.attempts + 1,
            lease_expires_at=at + lease,
            version=self.version + 1,
        )

    def release(self, at: datetime) -> "AnalysisJob":
        """Return a running job to pending so another attempt can claim it."""
        if self.status is not AnalysisStatus.RUNNING:
            msg = f"Only running analyses can be released, not {self.status}."
            raise ValueError(msg)
        return replace(
            self,
            status=AnalysisStatus.PENDING,
            updated_at=at,
            lease_expires_at=None,
            version=self.version + 1,
        )

    def transition(
        self,
        status: AnalysisStatus,
        at: datetime,
        *,
        result: AnalysisOutcome | None = None,
        error_code: str | None = None,
    ) -> "AnalysisJob":
        """Return a versioned job in a valid next state."""
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            msg = f"Cannot transition analysis from {self.status} to {status}."
            raise ValueError(msg)
        terminal = status in TERMINAL_STATUSES
        if status is AnalysisStatus.SUCCEEDED and result is None:
            msg = "A succeeded analysis requires a result."
            raise ValueError(msg)
        if status is AnalysisStatus.FAILED and not error_code:
            msg = "A failed analysis requires an error code."
            raise ValueError(msg)
        return replace(
            self,
            status=status,
            updated_at=at,
            completed_at=at if terminal else None,
            result=result,
            error_code=error_code,
            lease_expires_at=None if terminal else self.lease_expires_at,
            version=self.version + 1,
        )


class AnalysisRepository(Protocol):
    """Port for storing and retrieving analysis jobs."""

    def add(self, job: AnalysisJob) -> None:
        """Persist a new analysis job."""
        ...

    def enqueue(
        self, job: AnalysisJob, source_text: str, message: AnalysisJobMessage
    ) -> None:
        """Atomically persist a pending job, its source text, and an outbox entry."""
        ...

    def get_source_text(self, job_id: UUID) -> str | None:
        """Return the retained source text of a job that has not finished."""
        ...

    def get(self, job_id: UUID) -> AnalysisJob | None:
        """Return the job with the given identifier, if it exists."""
        ...

    def list(self, offset: int, limit: int) -> tuple[AnalysisJob, ...]:
        """Return one page of jobs, ordered newest first."""
        ...

    def count(self) -> int:
        """Return the number of retained jobs."""
        ...

    def update(self, job: AnalysisJob, expected_version: int) -> AnalysisJob:
        """Replace a job only when its persisted version matches.

        Moving a job to a terminal state also deletes its source text in the
        same transaction.
        """
        ...

    def delete_expired(self, before: datetime) -> int:
        """Delete jobs whose creation time is older than the retention cutoff."""
        ...


class AnalysisService:
    """Resolve analyzers and language models, run analyses, and record jobs."""

    def __init__(
        self,
        analyzers: Mapping[str, AnalyzerFactory],
        languages: Mapping[str, LanguageModel],
        repository: AnalysisRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._analyzers = analyzers
        self._languages = languages
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def _resolve(
        self, analyzer: str, language: str
    ) -> tuple[AnalyzerFactory, LanguageModel]:
        factory = self._analyzers.get(analyzer)
        if factory is None:
            raise UnsupportedAnalyzerError(analyzer, self._analyzers)
        model = self._languages.get(language)
        if model is None:
            raise UnsupportedLanguageError(language, self._languages)
        return factory, model

    def analyze(self, analyzer: str, text: str, language: str) -> AnalysisOutcome:
        """Run an analyzer synchronously and return its outcome."""
        factory, model = self._resolve(analyzer, language)
        return factory(model).analyze(text)

    def submit(
        self,
        analyzer: str,
        text: str,
        language: str,
        trace: TraceContext | None = None,
    ) -> AnalysisJob:
        """Validate a request and queue it as a pending job for a worker."""
        self._resolve(analyzer, language)
        job = AnalysisJob(
            id=self._id_factory(),
            analyzer=analyzer,
            language=language,
            status=AnalysisStatus.PENDING,
            created_at=self._clock(),
            completed_at=None,
            result=None,
            parameters={"language": language},
        )
        message = AnalysisJobMessage(job_id=job.id, trace=trace or TraceContext())
        self._repository.enqueue(job, text, message)
        return job

    def get(self, job_id: UUID) -> AnalysisJob:
        """Return a previously submitted job."""
        job = self._repository.get(job_id)
        if job is None:
            raise AnalysisNotFoundError(f"Analysis '{job_id}' was not found.")
        return job

    def list(self, offset: int, limit: int) -> tuple[AnalysisJob, ...]:
        """Return a page of retained analysis jobs."""
        return self._repository.list(offset, limit)

    def count(self) -> int:
        """Return the number of retained analysis jobs."""
        return self._repository.count()
