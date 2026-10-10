"""Tests for the analysis application service and in-memory repository."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisService,
    AnalysisStatus,
)
from codebreakers.application.errors import (
    AnalysisCapacityError,
    AnalysisNotFoundError,
    ConcurrentAnalysisUpdateError,
    UnsupportedAnalyzerError,
    UnsupportedLanguageError,
)
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
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
def test_submit_queues_pending_job_with_input_and_outbox_message() -> None:
    repository = InMemoryAnalysisRepository()
    service = _service(repository)
    trace = TraceContext(correlation_id="req-1", traceparent=None)

    job = service.submit("echo", "ABC", "test", trace)

    assert job.id == FIXED_ID
    assert job.status is AnalysisStatus.PENDING
    assert job.created_at == FIXED_TIME
    assert job.completed_at is None
    assert job.result is None
    assert service.get(FIXED_ID) == job
    assert repository.get_source_text(FIXED_ID) == "ABC"
    published: list[AnalysisJobMessage] = []
    assert repository.relay(published.append, limit=10) == 1
    assert published == [AnalysisJobMessage(job_id=FIXED_ID, trace=trace)]
    assert repository.relay(published.append, limit=10) == 0


@pytest.mark.unit
def test_submit_rejects_unsupported_names_before_queueing() -> None:
    repository = InMemoryAnalysisRepository()
    service = _service(repository)
    with pytest.raises(UnsupportedAnalyzerError):
        service.submit("nope", "ABC", "test")
    with pytest.raises(UnsupportedLanguageError):
        service.submit("echo", "ABC", "klingon")
    assert repository.count() == 0


@pytest.mark.unit
def test_concurrent_submissions_respect_unfinished_job_limit() -> None:
    repository = InMemoryAnalysisRepository()
    service = AnalysisService(
        analyzers={"echo": _factory},
        languages={"test": MODEL},
        repository=repository,
        max_unfinished=4,
    )

    def submit() -> bool:
        try:
            service.submit("echo", "ABC", "test")
        except AnalysisCapacityError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as executor:
        accepted = list(executor.map(lambda _: submit(), range(32)))

    assert sum(accepted) == 4
    assert repository.count_unfinished() == 4
    assert len({job.id for job in repository.list(0, 32)}) == 4


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


@pytest.mark.unit
def test_job_transitions_require_valid_data_and_increment_version() -> None:
    pending = AnalysisJob(
        id=FIXED_ID,
        analyzer="echo",
        language="test",
        status=AnalysisStatus.PENDING,
        created_at=FIXED_TIME,
        completed_at=None,
        result=None,
    )

    running = pending.transition(AnalysisStatus.RUNNING, FIXED_TIME)
    assert running.version == 2
    assert running.updated_at == FIXED_TIME
    with pytest.raises(ValueError, match="requires a result"):
        running.transition(AnalysisStatus.SUCCEEDED, FIXED_TIME)
    with pytest.raises(ValueError, match="requires an error code"):
        running.transition(AnalysisStatus.FAILED, FIXED_TIME)
    with pytest.raises(ValueError, match="Cannot transition"):
        running.transition(AnalysisStatus.PENDING, FIXED_TIME)


@pytest.mark.unit
def test_repository_paginates_updates_and_deletes_expired_jobs() -> None:
    repository = InMemoryAnalysisRepository(capacity=5)
    ids = [UUID(int=index) for index in range(1, 4)]
    for job_id in ids:
        repository.add(_job(job_id))

    assert repository.count() == 3
    assert tuple(job.id for job in repository.list(0, 2)) == tuple(reversed(ids))[:2]
    job = AnalysisJob(
        id=UUID(int=4),
        analyzer="echo",
        language="test",
        status=AnalysisStatus.PENDING,
        created_at=FIXED_TIME,
        completed_at=None,
        result=None,
    )
    repository.add(job)
    changed = job.transition(AnalysisStatus.RUNNING, FIXED_TIME)
    assert repository.update(changed, expected_version=job.version) == changed
    with pytest.raises(ConcurrentAnalysisUpdateError):
        repository.update(changed, expected_version=job.version)
    assert repository.delete_expired(FIXED_TIME.replace(year=2027)) == 4
    assert repository.count() == 0
