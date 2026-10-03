"""Tests for the worker, relay, and dead-letter commands and their wiring."""

import json
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from typer.testing import CliRunner

import codebreakers.worker.main as worker_main
from codebreakers.cli.exit_codes import ExitCode
from codebreakers.cli.main import app
from codebreakers.composition import create_analysis_service
from codebreakers.infrastructure.messaging.servicebus import (
    DeadLetterReport,
    DeadLetterSummary,
    ServiceBusJobConsumer,
)
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.worker.settings import WorkerSettings

runner = CliRunner()
_INSTALL_STOP_SIGNALS = worker_main.install_stop_signals


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODEBREAKERS_DATABASE_URL",
        "CODEBREAKERS_SERVICEBUS_CONNECTION_STRING",
        "CODEBREAKERS_WORKER_MAX_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker_main, "install_stop_signals", lambda _stop: None)


@pytest.mark.unit
@pytest.mark.parametrize("command", ["worker", "relay"])
def test_long_running_commands_require_configuration(command: str) -> None:
    result = runner.invoke(app, [command])
    assert result.exit_code == ExitCode.INVALID_USAGE
    assert "CODEBREAKERS_DATABASE_URL must be configured" in result.stderr


@pytest.mark.unit
def test_invalid_worker_settings_are_usage_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEBREAKERS_WORKER_MAX_ATTEMPTS", "lots")
    result = runner.invoke(app, ["worker"])
    assert result.exit_code == ExitCode.INVALID_USAGE
    assert "Invalid worker configuration" in result.stderr


@pytest.mark.unit
def test_deadletter_commands_require_service_bus() -> None:
    result = runner.invoke(app, ["deadletter", "list"])
    assert result.exit_code == ExitCode.INVALID_USAGE
    assert "CODEBREAKERS_SERVICEBUS_CONNECTION_STRING" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    "args", [["replay"], ["discard"], ["replay", "-s", "1", "--all"]]
)
def test_deadletter_settlement_requires_exactly_one_selection(args: list[str]) -> None:
    result = runner.invoke(app, ["deadletter", *args])
    assert result.exit_code == ExitCode.INVALID_USAGE
    assert "--sequence-number" in result.stderr


class FakeDeadLetterQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, frozenset[int] | None, int]] = []

    def list(self, max_count: int) -> list[DeadLetterSummary]:
        self.calls.append(("list", None, max_count))
        return [
            DeadLetterSummary(
                sequence_number=4,
                enqueued_at=datetime(2026, 10, 1, tzinfo=UTC),
                delivery_count=1,
                reason="retries-exhausted",
                description="ConnectionError",
                job_id=str(UUID(int=4)),
                attempt=5,
            )
        ]

    def replay(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        self.calls.append(("replay", sequence_numbers, max_count))
        return DeadLetterReport(settled=2, skipped=1)

    def discard(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        self.calls.append(("discard", sequence_numbers, max_count))
        return DeadLetterReport(settled=1, skipped=0)


@pytest.fixture
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> FakeDeadLetterQueue:
    queue = FakeDeadLetterQueue()

    @contextmanager
    def fake_dead_letter_queue(_settings: WorkerSettings) -> Iterator[Any]:
        yield queue

    monkeypatch.setattr(worker_main, "dead_letter_queue", fake_dead_letter_queue)
    return queue


@pytest.mark.unit
def test_deadletter_list_prints_metadata_as_json_lines(
    fake_queue: FakeDeadLetterQueue,
) -> None:
    result = runner.invoke(app, ["deadletter", "list", "--max", "5"])
    assert result.exit_code == ExitCode.SUCCESS
    line = json.loads(result.stdout.strip())
    assert line["sequence_number"] == 4
    assert line["job_id"] == str(UUID(int=4))
    assert fake_queue.calls == [("list", None, 5)]


@pytest.mark.unit
def test_deadletter_replay_and_discard_report_counts(
    fake_queue: FakeDeadLetterQueue,
) -> None:
    replayed = runner.invoke(app, ["deadletter", "replay", "-s", "4", "-s", "7"])
    discarded = runner.invoke(app, ["deadletter", "discard", "--all", "--max", "3"])

    assert replayed.exit_code == discarded.exit_code == ExitCode.SUCCESS
    assert replayed.stdout.strip() == "Replayed 2 message(s); skipped 1."
    assert discarded.stdout.strip() == "Discarded 1 message(s); skipped 0."
    assert fake_queue.calls == [
        ("replay", frozenset({4, 7}), 100),
        ("discard", None, 3),
    ]


# --- Process wiring ----------------------------------------------------------


class FakeSender:
    def __init__(self, stop: threading.Event | None = None) -> None:
        self.sent: list[str] = []
        self.stop = stop

    def __enter__(self) -> "FakeSender":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def send_messages(self, message: Any, /) -> None:
        self.sent.append(str(message))
        if self.stop is not None:
            self.stop.set()

    def schedule_messages(self, message: Any, at: datetime, /) -> list[int]:
        self.sent.append(str(message))
        return [1]


class FakeClient:
    def __init__(self, sender: FakeSender) -> None:
        self.sender = sender

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get_queue_sender(self, _queue_name: str) -> FakeSender:
        return self.sender


def _patch_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
    repository: InMemoryAnalysisRepository,
    client: FakeClient,
) -> None:
    @contextmanager
    def fake_repository(_settings: WorkerSettings) -> Iterator[Any]:
        yield repository

    monkeypatch.setattr(worker_main, "_postgres_repository", fake_repository)
    monkeypatch.setattr(worker_main, "_service_bus_client", lambda _settings: client)


@pytest.mark.unit
def test_run_relay_publishes_outbox_entries_until_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryAnalysisRepository()
    job = create_analysis_service(repository).submit(
        "caesar-bruteforce", "KHOOR", "english"
    )
    stop = threading.Event()
    sender = FakeSender(stop)
    _patch_infrastructure(monkeypatch, repository, FakeClient(sender))

    worker_main.run_relay(WorkerSettings(relay_poll_interval=0.01), stop)

    assert [json.loads(body)["job_id"] for body in sender.sent] == [str(job.id)]


@pytest.mark.unit
def test_run_relay_logs_and_backs_off_after_publish_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    repository = InMemoryAnalysisRepository()
    create_analysis_service(repository).submit("caesar-bruteforce", "KHOOR", "english")
    stop = threading.Event()

    class FailingSender(FakeSender):
        def send_messages(self, message: Any, /) -> None:
            stop.set()
            raise ConnectionError("broker down")

    _patch_infrastructure(monkeypatch, repository, FakeClient(FailingSender()))

    worker_main.run_relay(WorkerSettings(relay_poll_interval=0.01), stop)

    assert "outbox_relay_error error=ConnectionError" in caplog.text


@pytest.mark.unit
def test_run_worker_wires_consumer_with_lock_renewal_beyond_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryAnalysisRepository()
    client = FakeClient(FakeSender())
    _patch_infrastructure(monkeypatch, repository, client)
    calls: list[dict[str, Any]] = []

    def fake_run(self: ServiceBusJobConsumer, *args: Any, **kwargs: Any) -> None:
        calls.append({"args": args, **kwargs})

    monkeypatch.setattr(ServiceBusJobConsumer, "run", fake_run)
    stop = threading.Event()

    worker_main.run_worker(
        WorkerSettings(time_budget=timedelta(seconds=20), queue_name="jobs"), stop
    )

    assert calls[0]["args"] == (client, "jobs", stop)
    assert calls[0]["lock_renewal"] == timedelta(seconds=80)


@pytest.mark.unit
def test_stop_signals_set_the_stop_event() -> None:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    stop = threading.Event()
    try:
        _INSTALL_STOP_SIGNALS(stop)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
    finally:
        for sig, original in previous.items():
            signal.signal(sig, original)
    assert stop.is_set()
