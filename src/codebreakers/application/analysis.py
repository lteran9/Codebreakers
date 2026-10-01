"""Application service and ports for cryptanalysis jobs."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from codebreakers.application.errors import (
    AnalysisNotFoundError,
    UnsupportedAnalyzerError,
    UnsupportedLanguageError,
)
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
        terminal = status in {
            AnalysisStatus.SUCCEEDED,
            AnalysisStatus.FAILED,
            AnalysisStatus.CANCELLED,
        }
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
            version=self.version + 1,
        )


class AnalysisRepository(Protocol):
    """Port for storing and retrieving analysis jobs."""

    def add(self, job: AnalysisJob) -> None:
        """Persist a new analysis job."""
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
        """Replace a job only when its persisted version matches."""
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

    def analyze(self, analyzer: str, text: str, language: str) -> AnalysisOutcome:
        """Run an analyzer synchronously and return its outcome."""
        factory = self._analyzers.get(analyzer)
        if factory is None:
            raise UnsupportedAnalyzerError(analyzer, self._analyzers)
        model = self._languages.get(language)
        if model is None:
            raise UnsupportedLanguageError(language, self._languages)
        return factory(model).analyze(text)

    def submit(self, analyzer: str, text: str, language: str) -> AnalysisJob:
        """Run an analysis while persisting its validated lifecycle transitions."""
        factory = self._analyzers.get(analyzer)
        if factory is None:
            raise UnsupportedAnalyzerError(analyzer, self._analyzers)
        model = self._languages.get(language)
        if model is None:
            raise UnsupportedLanguageError(language, self._languages)
        created_at = self._clock()
        pending = AnalysisJob(
            id=self._id_factory(),
            analyzer=analyzer,
            language=language,
            status=AnalysisStatus.PENDING,
            created_at=created_at,
            completed_at=None,
            result=None,
            parameters={"language": language},
        )
        self._repository.add(pending)
        running = pending.transition(AnalysisStatus.RUNNING, self._clock())
        self._repository.update(running, expected_version=pending.version)
        try:
            outcome = factory(model).analyze(text)
        except Exception:
            failed = running.transition(
                AnalysisStatus.FAILED,
                self._clock(),
                error_code="analysis-failed",
            )
            self._repository.update(failed, expected_version=running.version)
            raise
        succeeded = running.transition(
            AnalysisStatus.SUCCEEDED, self._clock(), result=outcome
        )
        self._repository.update(succeeded, expected_version=running.version)
        return succeeded

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
