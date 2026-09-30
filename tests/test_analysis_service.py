"""Tests for the analysis application service and in-memory repository."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisService,
    AnalysisStatus,
)
from codebreakers.application.errors import (
    AnalysisNotFoundError,
    UnsupportedAnalyzerError,
    UnsupportedLanguageError,
)
from codebreakers.domain.cryptanalysis.models import (
    AnalysisResult,
    Analyzer,
    LanguageModel,
)
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository

MODEL = LanguageModel(name="test", version="v0", symbol_frequencies={})
FIXED_TIME = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
FIXED_ID = UUID("0b8f6a3e-2d43-4c1e-9f55-6a1d2b7c9e10")


class _EchoAnalyzer:
    def __init__(self, model: LanguageModel) -> None:
        self._model = model

    def analyze(self, text: str) -> AnalysisResult:
        return AnalysisResult(
            analyzer="echo",
            language=self._model.name,
            language_version=self._model.version,
            candidates=(),
        )


def _factory(model: LanguageModel) -> Analyzer[AnalysisResult]:
    return _EchoAnalyzer(model)


def _service(repository: InMemoryAnalysisRepository) -> AnalysisService:
    return AnalysisService(
        analyzers={"echo": _factory},
        languages={"test": MODEL},
        repository=repository,
        clock=lambda: FIXED_TIME,
        id_factory=lambda: FIXED_ID,
    )


def _job(job_id: UUID) -> AnalysisJob:
    return AnalysisJob(
        id=job_id,
        analyzer="echo",
        language="test",
        status=AnalysisStatus.SUCCEEDED,
        created_at=FIXED_TIME,
        completed_at=FIXED_TIME,
        result=None,
    )


@pytest.mark.unit
def test_submit_records_succeeded_job() -> None:
    repository = InMemoryAnalysisRepository()
    service = _service(repository)

    job = service.submit("echo", "ABC", "test")

    assert job.id == FIXED_ID
    assert job.status is AnalysisStatus.SUCCEEDED
    assert job.created_at == job.completed_at == FIXED_TIME
    assert isinstance(job.result, AnalysisResult)
    assert job.result.language_version == "v0"
    assert service.get(FIXED_ID) == job


@pytest.mark.unit
def test_analyze_does_not_record_a_job() -> None:
    repository = InMemoryAnalysisRepository()
    _service(repository).analyze("echo", "ABC", "test")
    assert repository.get(FIXED_ID) is None


@pytest.mark.unit
def test_unsupported_analyzer_lists_supported_names() -> None:
    with pytest.raises(UnsupportedAnalyzerError, match="Supported analyzers: echo"):
        _service(InMemoryAnalysisRepository()).analyze("nope", "ABC", "test")


@pytest.mark.unit
def test_unsupported_language_is_rejected() -> None:
    with pytest.raises(UnsupportedLanguageError):
        _service(InMemoryAnalysisRepository()).analyze("echo", "ABC", "klingon")


@pytest.mark.unit
def test_get_unknown_job_raises() -> None:
    with pytest.raises(AnalysisNotFoundError):
        _service(InMemoryAnalysisRepository()).get(FIXED_ID)


@pytest.mark.unit
def test_repository_evicts_oldest_job_at_capacity() -> None:
    repository = InMemoryAnalysisRepository(capacity=2)
    ids = [UUID(int=index) for index in range(3)]
    for job_id in ids:
        repository.add(_job(job_id))

    assert repository.get(ids[0]) is None
    assert repository.get(ids[1]) is not None
    assert repository.get(ids[2]) is not None


@pytest.mark.unit
def test_repository_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity"):
        InMemoryAnalysisRepository(capacity=0)
