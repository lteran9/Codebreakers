"""Tests for the file heartbeat used by worker and relay liveness probes."""

import os
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from codebreakers.application.messaging import JobPublisher
from codebreakers.application.processing import Action, Decision
from codebreakers.infrastructure.messaging.settlement import JobConsumer
from codebreakers.worker.heartbeat import Heartbeat, is_fresh, main

pytestmark = pytest.mark.unit


def test_beat_creates_and_refreshes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    Heartbeat(path).beat()
    assert path.exists()

    os.utime(path, (0, 0))
    Heartbeat(path).beat()
    assert is_fresh(path, max_age=5)


def test_freshness_depends_on_age_and_existence(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    assert not is_fresh(path, max_age=60)

    path.touch()
    os.utime(path, (1_000, 1_000))
    assert is_fresh(path, max_age=60, clock=lambda: 1_060)
    assert not is_fresh(path, max_age=60, clock=lambda: 1_061)


def test_beat_failures_are_logged_once_and_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    heartbeat = Heartbeat(tmp_path / "missing-dir" / "heartbeat")
    heartbeat.beat()
    heartbeat.beat()
    assert caplog.text.count("heartbeat_error error=FileNotFoundError") == 1


def test_probe_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "heartbeat"
    assert main([str(path)]) == 1
    assert "missing or stale" in capsys.readouterr().err

    path.touch()
    assert main([str(path), "--max-age", "30"]) == 0


class FakeReceiver:
    def __init__(self, stop: threading.Event, deliveries: int) -> None:
        self.stop = stop
        self.remaining = deliveries
        self.completed: list[str] = []

    def receive_messages(
        self, max_message_count: int | None = 1, max_wait_time: float | None = None
    ) -> Sequence["FakeMessage"]:
        if self.remaining == 0:
            self.stop.set()
            return []
        self.remaining -= 1
        return [FakeMessage()]

    def complete_message(self, message: "FakeMessage", /) -> None:
        self.completed.append(message.body)

    def abandon_message(self, message: "FakeMessage", /) -> None:
        raise AssertionError("unexpected abandon")

    def dead_letter_message(
        self,
        message: "FakeMessage",
        /,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None:
        raise AssertionError("unexpected dead letter")


class FakeMessage:
    body = "{}"
    sequence_number = 1
    enqueued_time_utc = None
    delivery_count = 1
    dead_letter_reason = None
    dead_letter_error_description = None


class NullPublisher:
    def publish(self, *_args: object) -> None:
        raise AssertionError("unexpected publish")


def test_consume_beats_once_per_receive_cycle_until_stopped() -> None:
    stop = threading.Event()
    receiver = FakeReceiver(stop, deliveries=2)
    beats: list[None] = []
    publisher: JobPublisher = NullPublisher()
    consumer = JobConsumer(lambda _body: Decision(Action.COMPLETE, "done"), publisher)

    consumer.consume(
        receiver, stop, max_wait_time=0, heartbeat=lambda: beats.append(None)
    )

    assert receiver.completed == ["{}", "{}"]
    assert len(beats) == 3
