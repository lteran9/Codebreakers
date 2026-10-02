"""Worker processing scenarios: duplicates, failures, budgets, and crashes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisOutcome,
    AnalysisStatus,
)
from codebreakers.application.errors import (
    AnalysisBudgetExceededError,
    AnalysisInterruptedError,
    AnalysisRejectedError,
    ConcurrentAnalysisUpdateError,
)
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.application.processing import (
    Action,
    AnalysisJobProcessor,
    ExecutionBudget,
    ProcessingStatus,
    RetryPolicy,
)
from codebreakers.domain.cryptanalysis.models import AnalysisResult
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.worker.handler import INVALID_MESSAGE, JobMessageHandler

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
JOB_ID = UUID(int=7)
BUDGET = ExecutionBudget(time_limit=timedelta(seconds=10))
GRACE = timedelta(seconds=5)
LEASE = BUDGET.time_limit + GRACE
RESULT = AnalysisResult(
    analyzer="caesar-bruteforce",
    language="english",
    language_version="v1",
    candidates=(),
)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class ScriptedExecutor:
    """Executor whose behaviour is a queue of results or exceptions."""

    def __init__(self, *steps: AnalysisOutcome | Exception) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, str, str]] = []
        self.before: Callable[[], None] | None = None

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisOutcome:
        self.calls.append((analyzer, language, text))
        if self.before is not None:
            self.before()
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step


class FlakyRepository(InMemoryAnalysisRepository):
    """Repository that fails the next ``failures`` reads like a lost database."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 0

    def get(self, job_id: UUID) -> AnalysisJob | None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("database unavailable")
        return super().get(job_id)


def _pending() -> AnalysisJob:
    return AnalysisJob(
        id=JOB_ID,
        analyzer="caesar-bruteforce",
        language="english",
        status=AnalysisStatus.PENDING,
        created_at=START,
        completed_at=None,
        result=None,
    )


def _setup(
    *steps: AnalysisOutcome | Exception,
    repository: InMemoryAnalysisRepository | None = None,
    max_attempts: int = 3,
) -> tuple[
    InMemoryAnalysisRepository,
    ScriptedExecutor,
    Clock,
    AnalysisJobProcessor,
    JobMessageHandler,
]:
    repo = repository or InMemoryAnalysisRepository()
    message = AnalysisJobMessage(job_id=JOB_ID, trace=TraceContext("req-1"))
    repo.enqueue(_pending(), "KHOOR", message)
    executor = ScriptedExecutor(*(steps or (RESULT,)))
    clock = Clock()
    processor = AnalysisJobProcessor(
        repo, executor, BUDGET, clock=clock, lease_grace=GRACE
    )
    policy = RetryPolicy(
        max_attempts=max_attempts,
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=3),
    )
    return repo, executor, clock, processor, JobMessageHandler(processor, policy)


def _message(attempt: int = 1) -> AnalysisJobMessage:
    return AnalysisJobMessage(
        job_id=JOB_ID, attempt=attempt, trace=TraceContext("req-1")
    )


def _job(repo: InMemoryAnalysisRepository) -> AnalysisJob:
    job = repo.get(JOB_ID)
    assert job is not None
    return job


# --- Job lease model ---------------------------------------------------------


@pytest.mark.unit
def test_claim_sets_lease_and_counts_attempts() -> None:
    claimed = _pending().claim(START, LEASE)
    assert claimed.status is AnalysisStatus.RUNNING
    assert claimed.lease_expires_at == START + LEASE
    assert (claimed.attempts, claimed.version) == (1, 2)
    assert not claimed.is_claimable(START + LEASE - timedelta(seconds=1))
    assert claimed.is_claimable(START + LEASE)
    with pytest.raises(ValueError, match="not claimable"):
        claimed.claim(START, LEASE)


@pytest.mark.unit
def test_release_returns_running_job_to_pending() -> None:
    released = _pending().claim(START, LEASE).release(START)
    assert released.status is AnalysisStatus.PENDING
    assert released.lease_expires_at is None
    assert released.attempts == 1
    with pytest.raises(ValueError, match="Only running"):
        released.release(START)


@pytest.mark.unit
def test_terminal_transition_clears_lease_and_running_without_lease_is_claimable() -> (
    None
):
    claimed = _pending().claim(START, LEASE)
    done = claimed.transition(AnalysisStatus.SUCCEEDED, START, result=RESULT)
    assert done.is_terminal
    assert done.lease_expires_at is None
    assert not done.is_claimable(START)
    legacy_running = _pending().transition(AnalysisStatus.RUNNING, START)
    assert legacy_running.is_claimable(START)


@pytest.mark.unit
def test_retry_policy_backs_off_exponentially_with_a_cap() -> None:
    policy = RetryPolicy(
        max_attempts=4, base_delay=timedelta(seconds=2), max_delay=timedelta(seconds=5)
    )
    assert [policy.delay_after(n).total_seconds() for n in (1, 2, 3)] == [2, 4, 5]
    assert [policy.can_retry(n) for n in (1, 3, 4)] == [True, True, False]


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay": timedelta(seconds=-1)},
        {"base_delay": timedelta(seconds=5), "max_delay": timedelta(seconds=1)},
    ],
)
def test_retry_policy_rejects_invalid_settings(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [{"time_limit": timedelta(0)}, {"memory_limit_bytes": 0}],
)
def test_execution_budget_rejects_non_positive_limits(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ExecutionBudget(**kwargs)  # type: ignore[arg-type]


# --- Processing outcomes -----------------------------------------------------


@pytest.mark.unit
def test_success_stores_result_and_deletes_source_text() -> None:
    repo, executor, _, _, handler = _setup(RESULT)

    decision = handler.handle(_message().to_json())

    assert (decision.action, decision.reason) == (Action.COMPLETE, "succeeded")
    job = _job(repo)
    assert job.status is AnalysisStatus.SUCCEEDED
    assert job.result == RESULT
    assert (job.attempts, job.version, job.lease_expires_at) == (1, 3, None)
    assert repo.get_source_text(JOB_ID) is None
    assert executor.calls == [("caesar-bruteforce", "english", "KHOOR")]


@pytest.mark.unit
def test_duplicate_delivery_after_completion_is_a_no_op() -> None:
    repo, executor, _, _, handler = _setup(RESULT)
    handler.handle(_message().to_json())
    finished = _job(repo)

    decision = handler.handle(_message().to_json())

    assert (decision.action, decision.reason) == (Action.COMPLETE, "already-finished")
    assert _job(repo) == finished
    assert len(executor.calls) == 1


@pytest.mark.unit
def test_concurrent_duplicate_is_deferred_until_the_lease_expires() -> None:
    repo, executor, clock, _, handler = _setup(RESULT)
    repo.update(_pending().claim(START, LEASE), expected_version=1)
    clock.advance(timedelta(seconds=4))

    decision = handler.handle(_message().to_json())

    assert decision.action is Action.RETRY
    assert decision.reason == "leased-by-another-worker"
    assert decision.message == _message()
    assert decision.delay == LEASE - timedelta(seconds=4)
    assert executor.calls == []


@pytest.mark.unit
def test_worker_terminated_mid_job_is_recovered_after_lease_expiry() -> None:
    repo, executor, clock, _, handler = _setup(RESULT)
    # A worker claimed the job and was killed before recording an outcome.
    repo.update(_pending().claim(START, LEASE), expected_version=1)
    assert repo.get_source_text(JOB_ID) == "KHOOR"

    clock.advance(LEASE)
    decision = handler.handle(_message().to_json())

    assert decision.action is Action.COMPLETE
    job = _job(repo)
    assert job.status is AnalysisStatus.SUCCEEDED
    assert job.attempts == 2


@pytest.mark.unit
def test_claim_conflict_is_deferred_briefly() -> None:
    class ConflictingRepository(InMemoryAnalysisRepository):
        def update(self, job: AnalysisJob, expected_version: int) -> AnalysisJob:
            raise ConcurrentAnalysisUpdateError("raced")

    repo = ConflictingRepository()
    _, executor, _, processor, _ = _setup(RESULT, repository=repo)

    result = processor.process(_message())

    assert result.status is ProcessingStatus.DEFERRED
    assert result.reason == "claim-conflict"
    assert executor.calls == []


@pytest.mark.unit
def test_result_is_dropped_when_another_worker_took_over() -> None:
    repo, executor, clock, processor, _ = _setup(RESULT)

    def lease_lapses_and_job_is_reclaimed() -> None:
        clock.advance(LEASE)
        current = _job(repo)
        repo.update(current.claim(clock(), LEASE), expected_version=current.version)

    executor.before = lease_lapses_and_job_is_reclaimed

    result = processor.process(_message())

    assert (result.status, result.reason) == (ProcessingStatus.DONE, "claim-lost")
    job = _job(repo)
    assert job.status is AnalysisStatus.RUNNING
    assert job.attempts == 2


@pytest.mark.unit
def test_time_budget_overrun_cancels_the_job() -> None:
    repo, _, _, _, handler = _setup(
        AnalysisBudgetExceededError("time-budget-exceeded", "too slow")
    )

    decision = handler.handle(_message().to_json())

    assert (decision.action, decision.reason) == (Action.COMPLETE, "cancelled")
    job = _job(repo)
    assert job.status is AnalysisStatus.CANCELLED
    assert job.error_code == "time-budget-exceeded"
    assert repo.get_source_text(JOB_ID) is None


@pytest.mark.unit
def test_permanent_analysis_failure_is_not_retried() -> None:
    repo, executor, _, _, handler = _setup(
        AnalysisRejectedError("insufficient-text", "short")
    )

    decision = handler.handle(_message().to_json())

    assert (decision.action, decision.reason) == (Action.COMPLETE, "failed")
    job = _job(repo)
    assert (job.status, job.error_code) == (AnalysisStatus.FAILED, "insufficient-text")
    assert len(executor.calls) == 1


@pytest.mark.unit
def test_missing_source_text_fails_the_job() -> None:
    repo, executor, _, _, handler = _setup(RESULT)
    repo._inputs.clear()

    handler.handle(_message().to_json())

    job = _job(repo)
    assert (job.status, job.error_code) == (AnalysisStatus.FAILED, "input-unavailable")
    assert executor.calls == []


@pytest.mark.unit
def test_unknown_job_is_completed_without_work() -> None:
    _, executor, _, _, handler = _setup(RESULT)
    message = AnalysisJobMessage(job_id=UUID(int=999))

    decision = handler.handle(message.to_json())

    assert (decision.action, decision.reason) == (Action.COMPLETE, "job-not-found")
    assert executor.calls == []


# --- Retries, poison messages, and dead-lettering ----------------------------


@pytest.mark.unit
def test_transient_failure_releases_the_job_and_retries_with_backoff() -> None:
    repo, executor, _, _, handler = _setup(
        AnalysisInterruptedError("analysis-interrupted", "child died"), RESULT
    )

    first = handler.handle(_message().to_json())

    assert first.action is Action.RETRY
    assert first.message == _message(attempt=2)
    assert first.delay == timedelta(seconds=1)
    released = _job(repo)
    assert released.status is AnalysisStatus.PENDING
    assert released.attempts == 1
    assert repo.get_source_text(JOB_ID) == "KHOOR"

    assert first.message is not None
    second = handler.handle(first.message.to_json())

    assert second.action is Action.COMPLETE
    assert _job(repo).status is AnalysisStatus.SUCCEEDED
    assert _job(repo).attempts == 2


@pytest.mark.unit
def test_infrastructure_failure_is_retried() -> None:
    repo = FlakyRepository()
    _, _, _, _, handler = _setup(RESULT, repository=repo)
    repo.failures = 1

    decision = handler.handle(_message().to_json())

    assert decision.action is Action.RETRY
    assert decision.reason == "transient-failure"
    assert decision.message == _message(attempt=2)


@pytest.mark.unit
def test_retries_are_bounded_then_job_fails_and_message_is_dead_lettered() -> None:
    repo, executor, _, _, handler = _setup(
        AnalysisInterruptedError("analysis-interrupted", "child died"), max_attempts=3
    )
    delays = []
    message = _message()
    for _ in range(2):
        decision = handler.handle(message.to_json())
        assert decision.action is Action.RETRY
        assert decision.message is not None
        delays.append(decision.delay.total_seconds())
        message = decision.message

    final = handler.handle(message.to_json())

    assert delays == [1, 2]
    assert final.action is Action.DEAD_LETTER
    assert final.reason == "retries-exhausted"
    assert final.description == "AnalysisInterruptedError"
    job = _job(repo)
    assert (job.status, job.error_code) == (AnalysisStatus.FAILED, "retries-exhausted")
    assert job.attempts == 3
    assert repo.get_source_text(JOB_ID) is None
    assert len(executor.calls) == 3


@pytest.mark.unit
def test_exhaustion_still_dead_letters_when_the_database_is_down() -> None:
    repo = FlakyRepository()
    _, _, _, _, handler = _setup(RESULT, repository=repo, max_attempts=1)
    repo.failures = 2

    decision = handler.handle(_message().to_json())

    assert (decision.action, decision.reason) == (
        Action.DEAD_LETTER,
        "retries-exhausted",
    )
    assert decision.description == "ConnectionError"
    assert _job(repo).status is AnalysisStatus.PENDING


@pytest.mark.unit
def test_fail_exhausted_ignores_finished_and_concurrently_finished_jobs() -> None:
    repo, _, _, processor, handler = _setup(RESULT)
    handler.handle(_message().to_json())
    finished = _job(repo)
    processor.fail_exhausted(_message())
    assert _job(repo) == finished

    class RacingRepository(InMemoryAnalysisRepository):
        def update(self, job: AnalysisJob, expected_version: int) -> AnalysisJob:
            raise ConcurrentAnalysisUpdateError("raced")

    racing = RacingRepository()
    _, _, _, racing_processor, _ = _setup(RESULT, repository=racing)
    racing_processor.fail_exhausted(_message())
    assert _job(racing).status is AnalysisStatus.PENDING


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [b"\x00garbage", '{"schema_version": 99, "job_id": "x", "attempt": 1}'],
)
def test_poison_messages_are_dead_lettered_immediately(body: str | bytes) -> None:
    _, executor, _, _, handler = _setup(RESULT)

    decision = handler.handle(body)

    assert (decision.action, decision.reason) == (Action.DEAD_LETTER, INVALID_MESSAGE)
    assert decision.description
    assert executor.calls == []


@pytest.mark.unit
def test_worker_logs_never_contain_source_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    _, _, _, _, handler = _setup(
        AnalysisInterruptedError("analysis-interrupted", "child died"), max_attempts=1
    )
    handler.handle(_message().to_json())
    assert "KHOOR" not in caplog.text
    assert "correlation_id=req-1" in caplog.text
