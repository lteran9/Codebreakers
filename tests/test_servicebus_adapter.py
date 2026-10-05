"""Unit tests for Service Bus message conversion, settlement, and dead letters."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.application.processing import Action, Decision
from codebreakers.infrastructure.messaging.servicebus import (
    MESSAGE_SUBJECT,
    ServiceBusJobConsumer,
    to_service_bus_message,
)
from codebreakers.infrastructure.messaging.settlement import (
    message_body,
    settle_dead_letters,
    summarize_dead_letter,
)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
ENQUEUED = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass
class FakeReceived:
    body: Any
    sequence_number: int | None = 1
    enqueued_time_utc: datetime | None = ENQUEUED
    delivery_count: int | None = 1
    dead_letter_reason: str | None = None
    dead_letter_error_description: str | None = None


@dataclass
class FakeReceiver:
    batches: list[list[FakeReceived]] = field(default_factory=list)
    completed: list[int | None] = field(default_factory=list)
    abandoned: list[int | None] = field(default_factory=list)
    dead_lettered: list[tuple[int | None, str | None, str | None]] = field(
        default_factory=list
    )
    fail_complete: bool = False
    fail_abandon: bool = False

    def receive_messages(
        self, max_message_count: int | None = 1, max_wait_time: float | None = None
    ) -> Sequence[FakeReceived]:
        return self.batches.pop(0) if self.batches else []

    def complete_message(self, message: FakeReceived, /) -> None:
        if self.fail_complete:
            raise ConnectionError("lock lost")
        self.completed.append(message.sequence_number)

    def abandon_message(self, message: FakeReceived, /) -> None:
        if self.fail_abandon:
            raise ConnectionError("link closed")
        self.abandoned.append(message.sequence_number)

    def dead_letter_message(
        self,
        message: FakeReceived,
        /,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None:
        self.dead_lettered.append((message.sequence_number, reason, error_description))


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[AnalysisJobMessage, timedelta]] = []

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        self.published.append((message, delay))


def _job_message(n: int = 1, attempt: int = 1) -> AnalysisJobMessage:
    return AnalysisJobMessage(
        job_id=UUID(int=n), attempt=attempt, trace=TraceContext("req-1", TRACEPARENT)
    )


@pytest.mark.unit
def test_service_bus_message_carries_schema_and_trace_metadata() -> None:
    outgoing = to_service_bus_message(_job_message(attempt=2))
    assert json.loads(str(outgoing))["job_id"] == str(UUID(int=1))
    assert outgoing.content_type == "application/json"
    assert outgoing.subject == MESSAGE_SUBJECT
    assert outgoing.correlation_id == "req-1"
    assert outgoing.application_properties == {
        "schema_version": 1,
        "job_id": str(UUID(int=1)),
        "attempt": 2,
        "traceparent": TRACEPARENT,
    }
    untraced = to_service_bus_message(AnalysisJobMessage(job_id=UUID(int=1)))
    assert untraced.application_properties is not None
    assert "traceparent" not in untraced.application_properties


@pytest.mark.unit
@pytest.mark.parametrize(
    "body", [b"abc", "abc", [b"a", b"bc"], (part for part in [b"ab", "c"])]
)
def test_message_body_accepts_sdk_body_shapes(body: Any) -> None:
    assert message_body(FakeReceived(body)) == b"abc"


@pytest.mark.unit
def test_message_body_falls_back_to_string_for_scalar_values() -> None:
    assert message_body(FakeReceived(42)) == b"42"


def _consumer(
    decision: Decision, publisher: RecordingPublisher
) -> ServiceBusJobConsumer:
    return ServiceBusJobConsumer(lambda _body: decision, publisher)


@pytest.mark.unit
def test_complete_decision_completes_the_delivery() -> None:
    receiver, publisher = FakeReceiver(), RecordingPublisher()
    _consumer(Decision(Action.COMPLETE, "succeeded"), publisher).settle(
        receiver, FakeReceived(b"{}", sequence_number=5)
    )
    assert receiver.completed == [5]
    assert publisher.published == []


@pytest.mark.unit
def test_retry_decision_schedules_next_attempt_before_completing() -> None:
    receiver, publisher = FakeReceiver(), RecordingPublisher()
    retry = _job_message(attempt=2)
    _consumer(
        Decision(Action.RETRY, "transient-failure", retry, timedelta(seconds=4)),
        publisher,
    ).settle(receiver, FakeReceived(b"{}", sequence_number=6))
    assert publisher.published == [(retry, timedelta(seconds=4))]
    assert receiver.completed == [6]


@pytest.mark.unit
def test_dead_letter_decision_records_reason_and_description() -> None:
    receiver = FakeReceiver()
    _consumer(
        Decision(Action.DEAD_LETTER, "invalid-message", description="bad schema"),
        RecordingPublisher(),
    ).settle(receiver, FakeReceived(b"x", sequence_number=7))
    assert receiver.dead_lettered == [(7, "invalid-message", "bad schema")]
    assert receiver.completed == []


@pytest.mark.unit
def test_failed_settlement_abandons_for_broker_redelivery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    receiver = FakeReceiver(fail_complete=True)
    _consumer(Decision(Action.COMPLETE, "succeeded"), RecordingPublisher()).settle(
        receiver, FakeReceived(b"{}", sequence_number=8)
    )
    assert receiver.abandoned == [8]
    assert "job_settlement_error error=ConnectionError" in caplog.text


@pytest.mark.unit
def test_failed_abandon_is_logged_and_left_to_lock_expiry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    receiver = FakeReceiver(fail_complete=True, fail_abandon=True)
    _consumer(Decision(Action.COMPLETE, "succeeded"), RecordingPublisher()).settle(
        receiver, FakeReceived(b"{}")
    )
    assert "job_abandon_error error=ConnectionError" in caplog.text


# --- Dead-letter operations --------------------------------------------------


def _dead(n: int, body: bytes | None = None) -> FakeReceived:
    return FakeReceived(
        body if body is not None else _job_message(n, attempt=5).to_json().encode(),
        sequence_number=n,
        delivery_count=3,
        dead_letter_reason="retries-exhausted",
        dead_letter_error_description="ConnectionError",
    )


@pytest.mark.unit
def test_summary_includes_metadata_but_never_raw_body() -> None:
    summary = summarize_dead_letter(_dead(4))
    assert summary.sequence_number == 4
    assert summary.job_id == str(UUID(int=4))
    assert summary.attempt == 5
    assert summary.reason == "retries-exhausted"
    assert summary.delivery_count == 3
    poison = summarize_dead_letter(_dead(9, body=b"SECRET PLAINTEXT"))
    assert (poison.job_id, poison.attempt) == (None, None)
    assert "SECRET" not in repr(poison)


class BrokerLikeReceiver:
    """Peek-lock receiver: abandoned messages become receivable again at once."""

    def __init__(self, messages: list[FakeReceived], batch_size: int = 2) -> None:
        self.available = list(messages)
        self.batch_size = batch_size
        self.completed: list[int | None] = []
        self.abandoned: list[int | None] = []

    def receive_messages(
        self, max_message_count: int | None = 1, max_wait_time: float | None = None
    ) -> Sequence[FakeReceived]:
        count = min(self.batch_size, max_message_count or 1)
        batch, self.available = self.available[:count], self.available[count:]
        return batch

    def complete_message(self, message: FakeReceived, /) -> None:
        self.completed.append(message.sequence_number)

    def abandon_message(self, message: FakeReceived, /) -> None:
        self.abandoned.append(message.sequence_number)
        self.available.insert(0, message)

    def dead_letter_message(
        self,
        message: FakeReceived,
        /,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None:
        raise AssertionError("dead letters are never dead-lettered again")


@pytest.mark.unit
def test_replay_finds_selected_messages_beyond_the_first_batch() -> None:
    receiver = BrokerLikeReceiver([_dead(n) for n in range(1, 8)], batch_size=2)
    publisher = RecordingPublisher()

    report = settle_dead_letters(
        receiver,
        sequence_numbers=frozenset({2, 6}),
        max_count=10,
        replay_to=publisher,
    )

    assert (report.settled, report.skipped) == (2, 0)
    assert [m.job_id.int for m, _ in publisher.published] == [2, 6]
    assert {m.attempt for m, _ in publisher.published} == {1}
    assert publisher.published[0][0].trace == TraceContext("req-1", TRACEPARENT)
    assert receiver.completed == [2, 6]
    assert sorted(n or 0 for n in receiver.abandoned) == [1, 3, 4, 5]
    assert sorted(m.sequence_number or 0 for m in receiver.available) == [1, 3, 4, 5, 7]


@pytest.mark.unit
def test_replay_skips_undecodable_messages() -> None:
    receiver = BrokerLikeReceiver([_dead(1, body=b"garbage"), _dead(2)])
    publisher = RecordingPublisher()

    report = settle_dead_letters(
        receiver, sequence_numbers=None, max_count=10, replay_to=publisher
    )

    assert (report.settled, report.skipped) == (1, 1)
    assert receiver.abandoned == [1]
    assert receiver.completed == [2]


@pytest.mark.unit
def test_discard_all_stops_at_max_count_and_releases_the_rest() -> None:
    receiver = BrokerLikeReceiver([_dead(n) for n in range(1, 5)], batch_size=3)

    report = settle_dead_letters(
        receiver, sequence_numbers=None, max_count=2, replay_to=None
    )

    assert report.settled == 2
    assert receiver.completed == [1, 2]
    assert receiver.abandoned == [3]


@pytest.mark.unit
def test_scan_stops_on_redelivery_and_at_the_scan_limit() -> None:
    redelivering = FakeReceiver(batches=[[_dead(1)], [_dead(1)], [_dead(2)]])
    report = settle_dead_letters(
        redelivering,
        sequence_numbers=frozenset({2}),
        max_count=10,
        replay_to=None,
        max_scan=2,
    )
    assert report.settled == 0
    assert redelivering.abandoned == [1, 1]


@pytest.mark.unit
def test_abandon_failures_after_a_scan_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    receiver = FakeReceiver(batches=[[_dead(1)]], fail_abandon=True)
    settle_dead_letters(
        receiver, sequence_numbers=frozenset({9}), max_count=1, replay_to=None
    )
    assert "dead_letter_abandon_error error=ConnectionError" in caplog.text
