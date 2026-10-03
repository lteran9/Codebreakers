"""Tests for the in-process runtime, outbox relay, and worker settings."""

import heapq
import time
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisOutcome,
    AnalysisStatus,
)
from codebreakers.application.errors import AnalysisInterruptedError
from codebreakers.application.messaging import AnalysisJobMessage
from codebreakers.application.processing import (
    AnalysisJobProcessor,
    ExecutionBudget,
    RetryPolicy,
)
from codebreakers.composition import create_analysis_service
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.worker.execution import InlineAnalysisExecutor
from codebreakers.worker.handler import JobMessageHandler, build_handler
from codebreakers.worker.inprocess import InProcessJobQueue, InProcessRuntime
from codebreakers.worker.settings import (
    ConfigurationError,
    QueueBackend,
    WorkerSettings,
)


def _runtime(
    repository: InMemoryAnalysisRepository,
    handler: JobMessageHandler | None = None,
) -> InProcessRuntime:
    return InProcessRuntime(
        repository,
        handler
        or build_handler(repository, WorkerSettings(), InlineAnalysisExecutor()),
        poll_interval=0.01,
    )


def _wait_for(repository: InMemoryAnalysisRepository, job_id: UUID) -> AnalysisJob:
    deadline = time.monotonic() + 10
    while True:
        job = repository.get(job_id)
        assert job is not None
        if job.is_terminal or time.monotonic() > deadline:
            return job
        time.sleep(0.01)


@pytest.mark.unit
def test_runtime_threads_relay_and_process_submitted_jobs() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository)
    runtime = _runtime(repository)
    runtime.start()
    try:
        assert runtime.is_running()
        job = service.submit("caesar-bruteforce", "KHOOR ZRUOG", "english")
        finished = _wait_for(repository, job.id)
    finally:
        runtime.stop()

    assert not runtime.is_running()
    assert finished.status is AnalysisStatus.SUCCEEDED
    assert repository.get_source_text(job.id) is None
    assert len(runtime.queue) == 0


@pytest.mark.unit
def test_run_until_idle_drains_outbox_and_queue() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository)
    runtime = _runtime(repository)
    ids = [service.submit("caesar-bruteforce", "KHOOR", "english").id for _ in range(3)]

    runtime.run_until_idle()

    assert all(_wait_for(repository, job_id).is_terminal for job_id in ids)


@pytest.mark.unit
def test_retries_are_delayed_and_poison_messages_are_dead_lettered() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository)

    class FlakyExecutor:
        failures = 1

        def execute(
            self, analyzer: str, language: str, text: str, budget: ExecutionBudget
        ) -> AnalysisOutcome:
            if self.failures:
                self.failures -= 1
                raise AnalysisInterruptedError("analysis-interrupted", "crashed")
            return InlineAnalysisExecutor().execute(analyzer, language, text, budget)

    processor = AnalysisJobProcessor(
        repository, FlakyExecutor(), ExecutionBudget(), lease_grace=timedelta(0)
    )
    policy = RetryPolicy(
        base_delay=timedelta(milliseconds=50), max_delay=timedelta(milliseconds=50)
    )
    runtime = _runtime(repository, JobMessageHandler(processor, policy))
    job = service.submit("caesar-bruteforce", "KHOOR", "english")
    runtime.queue.dead_letter("seed", "manual", None)
    runtime.queue.publish(AnalysisJobMessage(job_id=UUID(int=1)))

    runtime.run_until_idle()
    released = repository.get(job.id)
    assert released is not None
    assert released.status is AnalysisStatus.PENDING
    time.sleep(0.1)
    runtime.run_until_idle()

    finished = _wait_for(repository, job.id)
    assert finished.status is AnalysisStatus.SUCCEEDED
    assert finished.attempts == 2

    heapq.heappush(runtime.queue._heap, (0.0, -1, '{"schema_version": 42}'))
    runtime.run_until_idle()
    assert [d.reason for d in runtime.queue.dead_letters] == [
        "manual",
        "invalid-message",
    ]


@pytest.mark.unit
def test_restart_recovers_jobs_whose_queued_messages_were_lost() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository)
    queued = service.submit("caesar-bruteforce", "KHOOR", "english")
    abandoned = service.submit("caesar-bruteforce", "KHOOR", "english")
    finished = service.submit("caesar-bruteforce", "KHOOR", "english")
    crashed = _runtime(repository)
    assert crashed.relay_once() == 3  # outbox drained into memory only
    assert crashed.process_once()  # finishes the first job
    # Simulate a worker killed mid-job: claimed, lease already expired.
    current = repository.get(abandoned.id)
    assert current is not None
    repository.update(
        current.claim(current.created_at, timedelta(0)),
        expected_version=current.version,
    )
    unrelayed = service.submit("caesar-bruteforce", "KHOOR", "english")

    restarted = _runtime(repository)
    assert restarted.recover_once() == 2
    restarted.run_until_idle()

    for job_id in (queued.id, abandoned.id, finished.id, unrelayed.id):
        assert _wait_for(repository, job_id).status is AnalysisStatus.SUCCEEDED
    with pytest.raises(ValueError, match="limit"):
        repository.recover(lambda _m: None, limit=0)


@pytest.mark.unit
def test_runtime_start_runs_recovery_and_survives_its_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FlakyRecovery(InMemoryAnalysisRepository):
        failures = 2
        recovered = 0

        def recover(
            self, publish: Callable[[AnalysisJobMessage], None], limit: int
        ) -> int:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("db not ready")
            self.recovered += 1
            return super().recover(publish, limit)

    repository = FlakyRecovery()
    runtime = _runtime(repository)
    runtime.start()
    deadline = time.monotonic() + 5
    while not repository.recovered and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert runtime.is_running()
    runtime.stop()
    assert caplog.text.count("job_recovery_error error=ConnectionError") == 2
    assert repository.recovered == 1


@pytest.mark.unit
def test_queue_receive_waits_for_delayed_messages() -> None:
    queue = InProcessJobQueue()
    queue.publish(AnalysisJobMessage(job_id=UUID(int=1)), delay=timedelta(seconds=0.1))
    assert queue.receive(timeout=0) is None
    body = queue.receive(timeout=2)
    assert body is not None
    assert AnalysisJobMessage.from_json(body).job_id == UUID(int=1)
    assert queue.receive(timeout=0.01) is None


@pytest.mark.unit
def test_relay_keeps_unpublished_entries_after_a_failure() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository)
    ids = [service.submit("caesar-bruteforce", "KHOOR", "english").id for _ in range(3)]
    published: list[UUID] = []

    def flaky_publish(message: AnalysisJobMessage) -> None:
        if len(published) == 1:
            raise ConnectionError("broker down")
        published.append(message.job_id)

    with pytest.raises(ConnectionError):
        repository.relay(flaky_publish, limit=10)
    assert published == ids[:1]

    retried: list[UUID] = []
    assert repository.relay(lambda m: retried.append(m.job_id), limit=1) == 1
    assert repository.relay(lambda m: retried.append(m.job_id), limit=10) == 1
    assert retried == ids[1:]
    with pytest.raises(ValueError, match="limit"):
        repository.relay(lambda m: None, limit=0)


@pytest.mark.unit
def test_runtime_survives_relay_errors(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenOutbox(InMemoryAnalysisRepository):
        def relay(self, publish: object, limit: int) -> int:
            raise ConnectionError("db down")

    repository = BrokenOutbox()
    runtime = _runtime(repository)
    runtime.start()
    time.sleep(0.05)
    assert runtime.is_running()
    runtime.stop()
    assert "outbox_relay_error error=ConnectionError" in caplog.text


@pytest.mark.unit
def test_eviction_and_retention_drop_inputs_and_outbox_entries() -> None:
    repository = InMemoryAnalysisRepository(capacity=1)
    service = create_analysis_service(repository)
    first = service.submit("caesar-bruteforce", "AAA", "english")
    second = service.submit("caesar-bruteforce", "BBB", "english")
    assert repository.get_source_text(first.id) is None
    sent: list[AnalysisJobMessage] = []
    assert repository.relay(sent.append, limit=10) == 1
    assert sent[0].job_id == second.id
    assert repository.delete_expired(second.created_at + timedelta(seconds=1)) == 1
    assert repository.get_source_text(second.id) is None


# --- Settings ----------------------------------------------------------------


@pytest.mark.unit
def test_settings_defaults_and_environment_overrides() -> None:
    assert WorkerSettings.from_env({}) == WorkerSettings()
    settings = WorkerSettings.from_env(
        {
            "CODEBREAKERS_ANALYSIS_QUEUE": "service-bus",
            "CODEBREAKERS_DATABASE_URL": "postgresql://u:secret@db/x",
            "CODEBREAKERS_SERVICEBUS_CONNECTION_STRING": "Endpoint=sb://x;Key=secret",
            "CODEBREAKERS_SERVICEBUS_QUEUE": "jobs",
            "CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS": "12.5",
            "CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB": "0",
            "CODEBREAKERS_WORKER_MAX_ATTEMPTS": "2",
            "CODEBREAKERS_WORKER_RETRY_BASE_SECONDS": "1",
            "CODEBREAKERS_WORKER_RETRY_MAX_SECONDS": "8",
            "CODEBREAKERS_ANALYSIS_RETENTION_DAYS": "3",
        }
    )
    assert settings.queue_backend is QueueBackend.SERVICE_BUS
    assert settings.queue_name == "jobs"
    assert settings.budget() == ExecutionBudget(timedelta(seconds=12.5), None)
    assert settings.retry_policy() == RetryPolicy(
        2, timedelta(seconds=1), timedelta(seconds=8)
    )
    assert settings.retention_days == 3
    assert settings.require_database_url().endswith("/x")
    assert settings.require_servicebus().startswith("Endpoint")
    assert "secret" not in repr(settings)
    assert WorkerSettings().budget().memory_limit_bytes == 1024 * 1024 * 1024


@pytest.mark.unit
@pytest.mark.parametrize(
    "env",
    [
        {"CODEBREAKERS_ANALYSIS_QUEUE": "kafka"},
        {"CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS": "0"},
        {"CODEBREAKERS_WORKER_MAX_ATTEMPTS": "many"},
        {"CODEBREAKERS_WORKER_RETRY_MAX_SECONDS": "0"},
        {"CODEBREAKERS_ANALYSIS_RETENTION_DAYS": "0"},
    ],
)
def test_invalid_settings_raise_configuration_error(env: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        WorkerSettings.from_env(env)


@pytest.mark.unit
def test_missing_connection_settings_name_the_variable() -> None:
    with pytest.raises(ConfigurationError, match="CODEBREAKERS_DATABASE_URL"):
        WorkerSettings().require_database_url()
    with pytest.raises(ConfigurationError, match="SERVICEBUS_CONNECTION_STRING"):
        WorkerSettings().require_servicebus()
