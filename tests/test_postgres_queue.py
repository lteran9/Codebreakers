"""PostgreSQL job queue integration tests against a real database."""

import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session, sessionmaker

from codebreakers.api.app import ApiSettings, create_app
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.infrastructure.messaging.postgres_queue import (
    MAX_DELIVERY_COUNT_EXCEEDED,
    MessageLockLostError,
    PostgresDeadLetterQueue,
    PostgresJobPublisher,
    PostgresQueueMessage,
    PostgresQueueReceiver,
    PostgresQueueStats,
)
from codebreakers.infrastructure.persistence.postgres import (
    JobQueueRecord,
    create_postgres_engine,
)
from codebreakers.infrastructure.telemetry import QueueStats
from codebreakers.worker.main import run_relay, run_worker
from codebreakers.worker.settings import QueueBackend, WorkerSettings

pytestmark = pytest.mark.integration

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.fixture
def sessions(database_engine: Engine) -> sessionmaker[Session]:
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE analysis_job_queue"))
        connection.execute(text("TRUNCATE analysis_jobs CASCADE"))
    return sessionmaker(database_engine)


def _message(n: int, attempt: int = 1) -> AnalysisJobMessage:
    return AnalysisJobMessage(
        job_id=UUID(int=n), attempt=attempt, trace=TraceContext(f"req-{n}", TRACEPARENT)
    )


def _decode(message: PostgresQueueMessage) -> AnalysisJobMessage:
    return AnalysisJobMessage.from_json(message.body)


def _expire_locks(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.execute(
            update(JobQueueRecord).values(
                locked_until=JobQueueRecord.enqueued_at - timedelta(seconds=1)
            )
        )


def _dead_letter(
    sessions: sessionmaker[Session], body: AnalysisJobMessage | str, reason: str
) -> int:
    payload = body if isinstance(body, str) else body.to_json()
    with sessions.begin() as session:
        record = JobQueueRecord(
            payload=payload,
            enqueued_at=text("now()"),
            available_at=text("now()"),
            dead_lettered_at=text("now()"),
            dead_letter_reason=reason,
            dead_letter_description="ConnectionError",
            delivery_count=5,
        )
        session.add(record)
        session.flush()
        return record.id


def test_received_message_is_locked_until_completed(
    sessions: sessionmaker[Session],
) -> None:
    PostgresJobPublisher(sessions).publish(_message(1, attempt=2))
    receiver = PostgresQueueReceiver(sessions)

    (message,) = receiver.receive_messages()
    assert _decode(message) == _message(1, attempt=2)
    assert message.delivery_count == 1
    assert receiver.receive_messages(max_wait_time=0) == []

    receiver.complete_message(message)
    _expire_locks(sessions)
    assert receiver.receive_messages() == []


def test_delayed_message_waits_until_due(sessions: sessionmaker[Session]) -> None:
    publisher = PostgresJobPublisher(sessions)
    publisher.publish(_message(1), delay=timedelta(seconds=1))
    receiver = PostgresQueueReceiver(sessions, poll_interval=0.1)

    assert receiver.receive_messages(max_wait_time=0) == []
    (message,) = receiver.receive_messages(max_wait_time=3)
    assert _decode(message) == _message(1)


def test_abandoned_message_is_redelivered_with_a_new_lock(
    sessions: sessionmaker[Session],
) -> None:
    PostgresJobPublisher(sessions).publish(_message(1))
    receiver = PostgresQueueReceiver(sessions)

    (first,) = receiver.receive_messages()
    receiver.abandon_message(first)
    (second,) = receiver.receive_messages()

    assert second.sequence_number == first.sequence_number
    assert second.delivery_count == 2
    assert second.lock_token != first.lock_token
    with pytest.raises(MessageLockLostError):
        receiver.complete_message(first)
    receiver.complete_message(second)


def test_expired_lock_is_taken_over_and_the_old_receiver_cannot_settle(
    sessions: sessionmaker[Session],
) -> None:
    PostgresJobPublisher(sessions).publish(_message(1))
    slow = PostgresQueueReceiver(sessions)
    (stale,) = slow.receive_messages()

    _expire_locks(sessions)
    (current,) = PostgresQueueReceiver(sessions).receive_messages()

    with pytest.raises(MessageLockLostError):
        slow.complete_message(stale)
    with pytest.raises(MessageLockLostError):
        slow.dead_letter_message(stale, reason="late")
    slow.complete_message(current)


def test_unlocked_messages_cannot_be_settled(sessions: sessionmaker[Session]) -> None:
    PostgresJobPublisher(sessions).publish(_message(1))
    receiver = PostgresQueueReceiver(sessions)
    (message,) = receiver.receive_messages()
    unlocked = PostgresQueueMessage(
        sequence_number=message.sequence_number,
        body=message.body,
        enqueued_time_utc=message.enqueued_time_utc,
        delivery_count=message.delivery_count,
    )
    with pytest.raises(MessageLockLostError, match="with a lock"):
        receiver.complete_message(unlocked)


def test_message_delivered_too_often_is_dead_lettered(
    sessions: sessionmaker[Session],
) -> None:
    PostgresJobPublisher(sessions).publish(_message(1))
    receiver = PostgresQueueReceiver(sessions, max_delivery_count=2)
    for _ in range(2):
        (message,) = receiver.receive_messages()
        _expire_locks(sessions)

    assert receiver.receive_messages() == []
    (summary,) = PostgresDeadLetterQueue(sessions).list(10)
    assert summary.reason == MAX_DELIVERY_COUNT_EXCEEDED
    assert summary.delivery_count == 2
    assert summary.job_id == str(UUID(int=1))
    assert message.delivery_count == 2


def test_concurrent_receivers_never_share_a_message(
    sessions: sessionmaker[Session],
) -> None:
    publisher = PostgresJobPublisher(sessions)
    for n in range(1, 41):
        publisher.publish(_message(n))
    received: list[int] = []
    lock = threading.Lock()

    def drain() -> None:
        receiver = PostgresQueueReceiver(sessions)
        while batch := receiver.receive_messages(max_message_count=3):
            for message in batch:
                receiver.complete_message(message)
                with lock:
                    received.append(_decode(message).job_id.int)

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(received) == list(range(1, 41))


def test_queue_stats_report_depth_oldest_ready_age_and_dead_letters(
    sessions: sessionmaker[Session],
) -> None:
    stats = PostgresQueueStats(sessions)
    assert stats.read() == QueueStats(0, 0.0, 0)

    publisher = PostgresJobPublisher(sessions)
    publisher.publish(_message(1))
    publisher.publish(_message(2), delay=timedelta(hours=1))
    _dead_letter(sessions, _message(3), MAX_DELIVERY_COUNT_EXCEEDED)
    with sessions.begin() as session:
        session.execute(
            update(JobQueueRecord)
            .where(JobQueueRecord.dead_lettered_at.is_(None))
            .where(JobQueueRecord.available_at <= text("now()"))
            .values(available_at=text("now() - interval '90 seconds'"))
        )

    current = stats.read()
    assert (current.depth, current.dead_letters) == (2, 1)
    # The delayed message is not ready yet, so it does not age the queue.
    assert 90 <= current.oldest_ready_age_seconds < 120


def test_database_spans_name_the_server_without_credentials(
    database_engine: Engine, span_exporter: InMemorySpanExporter
) -> None:
    password = "pw-sentinel-5f3a"
    with database_engine.begin() as connection:
        connection.execute(text("DROP ROLE IF EXISTS span_probe"))
        connection.execute(text(f"CREATE ROLE span_probe LOGIN PASSWORD '{password}'"))
    url = database_engine.url.set(username="span_probe", password=password)
    engine = create_postgres_engine(url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT :probe"), {"probe": "value-sentinel"})
    finally:
        engine.dispose()

    (span,) = span_exporter.get_finished_spans()
    assert span.attributes is not None
    assert span.attributes["db.system"] == "postgresql"
    assert span.attributes["server.address"] == url.host
    exported = span.to_json()
    assert password not in exported
    assert "span_probe" not in exported
    assert "value-sentinel" not in exported


def test_dead_lettered_message_is_listed_without_its_body(
    sessions: sessionmaker[Session],
) -> None:
    PostgresJobPublisher(sessions).publish(_message(1, attempt=5))
    receiver = PostgresQueueReceiver(sessions)
    (message,) = receiver.receive_messages()
    receiver.dead_letter_message(
        message, reason="retries-exhausted", error_description="OperationalError"
    )

    assert receiver.receive_messages() == []
    (summary,) = PostgresDeadLetterQueue(sessions).list(10)
    assert summary.sequence_number == message.sequence_number
    assert summary.reason == "retries-exhausted"
    assert summary.description == "OperationalError"
    assert summary.attempt == 5
    assert summary.enqueued_at is not None


def test_replay_republishes_selected_dead_letters_as_first_attempts(
    sessions: sessionmaker[Session],
) -> None:
    first = _dead_letter(sessions, _message(1, attempt=5), "retries-exhausted")
    second = _dead_letter(sessions, _message(2, attempt=5), "retries-exhausted")
    queue = PostgresDeadLetterQueue(sessions)

    report = queue.replay(frozenset({second}), max_count=10)

    assert (report.settled, report.skipped) == (1, 0)
    assert [s.sequence_number for s in queue.list(10)] == [first]
    receiver = PostgresQueueReceiver(sessions)
    (replayed,) = receiver.receive_messages()
    assert _decode(replayed) == _message(2, attempt=1)
    assert replayed.delivery_count == 1


def test_replay_skips_undecodable_dead_letters_and_discard_removes_them(
    sessions: sessionmaker[Session],
) -> None:
    poison = _dead_letter(sessions, "not json", "invalid-message")
    _dead_letter(sessions, _message(1), "retries-exhausted")
    queue = PostgresDeadLetterQueue(sessions)

    report = queue.replay(None, max_count=10)
    assert (report.settled, report.skipped) == (1, 1)
    (remaining,) = queue.list(10)
    assert remaining.sequence_number == poison
    assert remaining.job_id is None

    discarded = queue.discard(None, max_count=10)
    assert (discarded.settled, discarded.skipped) == (1, 0)
    assert queue.list(10) == []


def test_publisher_contract_round_trip_preserves_trace(
    sessions: sessionmaker[Session],
) -> None:
    publisher = PostgresJobPublisher(sessions)
    publisher.publish(_message(1), delay=timedelta(seconds=-5))
    (message,) = PostgresQueueReceiver(sessions).receive_messages()
    assert _decode(message).trace == TraceContext("req-1", TRACEPARENT)


@pytest.fixture
def distributed_stack(
    sessions: sessionmaker[Session], database_engine: Engine
) -> Iterator[TestClient]:
    url = database_engine.url.render_as_string(hide_password=False)
    settings = WorkerSettings(
        queue_backend=QueueBackend.POSTGRES,
        database_url=url,
        relay_poll_interval=0.05,
    )
    stop = threading.Event()
    threads = [
        threading.Thread(target=target, args=(settings, stop), daemon=True)
        for target in (run_relay, run_worker)
    ]
    for thread in threads:
        thread.start()
    app = create_app(ApiSettings(database_url=url, worker=settings))
    try:
        with TestClient(app) as client:
            yield client
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
    assert not any(thread.is_alive() for thread in threads)


def test_api_relay_and_worker_complete_a_job_over_the_postgres_queue(
    distributed_stack: TestClient,
) -> None:
    response = distributed_stack.post(
        "/v1/analyses",
        json={
            "analyzer": "caesar-bruteforce",
            "text": "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ",
        },
        headers={"X-Request-ID": "smoke-1", "traceparent": TRACEPARENT},
    )
    assert response.status_code == 202
    location = response.headers["location"]

    deadline = time.monotonic() + 30
    job = response.json()
    while job["status"] in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(0.2)
        job = distributed_stack.get(location).json()

    assert job["status"] == "succeeded"
    assert job["result"]["candidates"][0]["key"] == "3"
