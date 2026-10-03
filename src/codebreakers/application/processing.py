"""Worker-side use case: claim a queued analysis job, run it, and record the outcome."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisOutcome,
    AnalysisRepository,
    AnalysisStatus,
)
from codebreakers.application.errors import (
    AnalysisBudgetExceededError,
    AnalysisInterruptedError,
    AnalysisRejectedError,
    ConcurrentAnalysisUpdateError,
)
from codebreakers.application.messaging import AnalysisJobMessage

logger = logging.getLogger("codebreakers.worker")

INPUT_UNAVAILABLE = "input-unavailable"
RETRIES_EXHAUSTED = "retries-exhausted"


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """Per-job limits enforced while an analyzer runs."""

    time_limit: timedelta = timedelta(seconds=30)
    memory_limit_bytes: int | None = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.time_limit <= timedelta(0):
            msg = "time_limit must be positive."
            raise ValueError(msg)
        if self.memory_limit_bytes is not None and self.memory_limit_bytes < 1:
            msg = "memory_limit_bytes must be positive when set."
            raise ValueError(msg)


class AnalysisExecutor(Protocol):
    """Port that runs a registered analyzer within a budget."""

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisOutcome:
        """Return the outcome or raise an ``AnalysisExecutionError`` subclass.

        ``AnalysisBudgetExceededError`` and ``AnalysisRejectedError`` are final.
        ``AnalysisInterruptedError`` means a retry may succeed.
        """
        ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retries with capped exponential backoff."""

    max_attempts: int = 5
    base_delay: timedelta = timedelta(seconds=2)
    max_delay: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1."
            raise ValueError(msg)
        if self.base_delay < timedelta(0) or self.max_delay < self.base_delay:
            msg = "Delays must be non-negative and max_delay >= base_delay."
            raise ValueError(msg)

    def can_retry(self, attempt: int) -> bool:
        """Return whether a failed ``attempt`` may be followed by another."""
        return attempt < self.max_attempts

    def delay_after(self, attempt: int) -> timedelta:
        """Return the backoff before the attempt that follows ``attempt``."""
        backoff: timedelta = self.base_delay * (2 ** (attempt - 1))
        return min(backoff, self.max_delay)


class ProcessingStatus(StrEnum):
    """What the transport should do with the message after processing."""

    DONE = "done"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    """Outcome of processing one message; ``delay`` applies when deferred."""

    status: ProcessingStatus
    reason: str
    delay: timedelta = timedelta(0)


class Action(StrEnum):
    """How a transport must settle the message it delivered."""

    COMPLETE = "complete"
    RETRY = "retry"
    DEAD_LETTER = "dead-letter"


@dataclass(frozen=True, slots=True)
class Decision:
    """Settlement for one delivery.

    For ``RETRY``, publish ``message`` after ``delay`` and then complete the
    original delivery. ``reason`` and ``description`` never contain job input.
    """

    action: Action
    reason: str
    message: AnalysisJobMessage | None = None
    delay: timedelta = timedelta(0)
    description: str | None = None


class AnalysisJobProcessor:
    """Idempotently process job messages under at-least-once delivery."""

    def __init__(
        self,
        repository: AnalysisRepository,
        executor: AnalysisExecutor,
        budget: ExecutionBudget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_grace: timedelta = timedelta(seconds=15),
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._budget = budget or ExecutionBudget()
        self._clock = clock
        # The lease outlives the time budget, so a live worker never loses it.
        self._lease = self._budget.time_limit + lease_grace

    def process(self, message: AnalysisJobMessage) -> ProcessingResult:
        """Claim the job, run it within budget, and store a terminal outcome.

        Raises ``AnalysisInterruptedError`` or infrastructure errors when the
        work should be retried.
        """
        job = self._repository.get(message.job_id)
        if job is None:
            return ProcessingResult(ProcessingStatus.DONE, "job-not-found")
        if job.is_terminal:
            return ProcessingResult(ProcessingStatus.DONE, "already-finished")
        now = self._clock()
        if not job.is_claimable(now):
            assert job.lease_expires_at is not None
            return ProcessingResult(
                ProcessingStatus.DEFERRED,
                "leased-by-another-worker",
                job.lease_expires_at - now,
            )
        claimed = job.claim(now, self._lease)
        try:
            self._repository.update(claimed, expected_version=job.version)
        except ConcurrentAnalysisUpdateError:
            return ProcessingResult(
                ProcessingStatus.DEFERRED, "claim-conflict", timedelta(seconds=1)
            )

        text = self._repository.get_source_text(job.id)
        if text is None:
            return self._finish(
                claimed, AnalysisStatus.FAILED, error_code=INPUT_UNAVAILABLE
            )
        try:
            outcome = self._executor.execute(
                claimed.analyzer, claimed.language, text, self._budget
            )
        except AnalysisBudgetExceededError as err:
            return self._finish(
                claimed, AnalysisStatus.CANCELLED, error_code=err.error_code
            )
        except AnalysisRejectedError as err:
            return self._finish(
                claimed, AnalysisStatus.FAILED, error_code=err.error_code
            )
        except AnalysisInterruptedError:
            self._release(claimed)
            raise
        return self._finish(claimed, AnalysisStatus.SUCCEEDED, result=outcome)

    def fail_exhausted(self, message: AnalysisJobMessage) -> None:
        """Mark a job failed after its final retry, unless it already finished."""
        job = self._repository.get(message.job_id)
        if job is None or job.is_terminal:
            return
        failed = job.transition(
            AnalysisStatus.FAILED, self._clock(), error_code=RETRIES_EXHAUSTED
        )
        try:
            self._repository.update(failed, expected_version=job.version)
        except ConcurrentAnalysisUpdateError:
            logger.info("job_finished_concurrently job_id=%s", job.id)

    def _finish(
        self,
        claimed: AnalysisJob,
        status: AnalysisStatus,
        *,
        result: AnalysisOutcome | None = None,
        error_code: str | None = None,
    ) -> ProcessingResult:
        finished = claimed.transition(
            status, self._clock(), result=result, error_code=error_code
        )
        try:
            self._repository.update(finished, expected_version=claimed.version)
        except ConcurrentAnalysisUpdateError:
            # The lease lapsed and another worker took over, so drop this result.
            return ProcessingResult(ProcessingStatus.DONE, "claim-lost")
        return ProcessingResult(ProcessingStatus.DONE, status.value)

    def _release(self, claimed: AnalysisJob) -> None:
        try:
            self._repository.update(
                claimed.release(self._clock()), expected_version=claimed.version
            )
        except ConcurrentAnalysisUpdateError:
            logger.info("job_release_conflict job_id=%s", claimed.id)
