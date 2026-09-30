"""Application service and ports for cryptanalysis jobs."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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


class AnalysisRepository(Protocol):
    """Port for storing and retrieving analysis jobs."""

    def add(self, job: AnalysisJob) -> None:
        """Persist a new analysis job."""
        ...

    def get(self, job_id: UUID) -> AnalysisJob | None:
        """Return the job with the given identifier, if it exists."""
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
        """Run an analysis and record it as a completed job."""
        created_at = self._clock()
        outcome = self.analyze(analyzer, text, language)
        job = AnalysisJob(
            id=self._id_factory(),
            analyzer=analyzer,
            language=language,
            status=AnalysisStatus.SUCCEEDED,
            created_at=created_at,
            completed_at=self._clock(),
            result=outcome,
        )
        self._repository.add(job)
        return job

    def get(self, job_id: UUID) -> AnalysisJob:
        """Return a previously submitted job."""
        job = self._repository.get(job_id)
        if job is None:
            raise AnalysisNotFoundError(f"Analysis '{job_id}' was not found.")
        return job
