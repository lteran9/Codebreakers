"""Shared ``JobPublisher`` contract run against every adapter.

Each adapter is wrapped in a harness that can publish, advance time, and
receive what a consumer would see. The PostgreSQL queue runs against a
Testcontainers database. The Service Bus contract also runs against a real
namespace when ``CODEBREAKERS_TEST_SERVICEBUS_CONNECTION_STRING`` is set (see
the manually triggered ``servicebus-integration`` workflow).
"""

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import pytest
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from codebreakers.application.messaging import (
    AnalysisJobMessage,
    JobPublisher,
    TraceContext,
)
from codebreakers.infrastructure.messaging.postgres_queue import (
    PostgresJobPublisher,
    PostgresQueueReceiver,
)
from codebreakers.infrastructure.messaging.servicebus import ServiceBusJobPublisher
from codebreakers.infrastructure.messaging.settlement import message_body
from codebreakers.worker.inprocess import InProcessJobQueue

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
SERVICEBUS_ENV = "CODEBREAKERS_TEST_SERVICEBUS_CONNECTION_STRING"


class PublisherHarness(Protocol):
    publisher: JobPublisher
    delay_unit: timedelta

    def advance(self, delta: timedelta) -> None: ...

    def receive_due(self) -> list[AnalysisJobMessage]: ...


class InProcessHarness:
    delay_unit = timedelta(milliseconds=200)

    def __init__(self) -> None:
        self.queue = InProcessJobQueue()
        self.publisher: JobPublisher = self.queue

    def advance(self, delta: timedelta) -> None:
        time.sleep(delta.total_seconds())

    def receive_due(self) -> list[AnalysisJobMessage]:
        received = []
        while (body := self.queue.receive()) is not None:
            received.append(AnalysisJobMessage.from_json(body))
        return received


class FakeSender:
    """In-memory stand-in for ``ServiceBusSender`` with a controllable clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, tzinfo=UTC)
        self.sent: list[tuple[datetime, ServiceBusMessage]] = []

    def send_messages(self, message: ServiceBusMessage, /) -> None:
        self.sent.append((self.now, message))

    def schedule_messages(
        self, messages: ServiceBusMessage, schedule_time_utc: datetime, /
    ) -> list[int]:
        self.sent.append((schedule_time_utc, messages))
        return [len(self.sent)]


class FakeServiceBusHarness:
    delay_unit = timedelta(seconds=30)

    def __init__(self) -> None:
        self.sender = FakeSender()
        self.publisher: JobPublisher = ServiceBusJobPublisher(
            self.sender, clock=lambda: self.sender.now
        )

    def advance(self, delta: timedelta) -> None:
        self.sender.now += delta

    def receive_due(self) -> list[AnalysisJobMessage]:
        due = [entry for entry in self.sender.sent if entry[0] <= self.sender.now]
        self.sender.sent = [e for e in self.sender.sent if e not in due]
        due.sort(key=lambda entry: entry[0])
        return [AnalysisJobMessage.from_json(str(message)) for _, message in due]


class RealServiceBusHarness:
    delay_unit = timedelta(seconds=3)

    def __init__(self, client: ServiceBusClient, queue_name: str) -> None:
        self._client = client
        self._queue_name = queue_name
        self._sender = client.get_queue_sender(queue_name)
        self.publisher: JobPublisher = ServiceBusJobPublisher(self._sender)

    def close(self) -> None:
        self._sender.close()

    def advance(self, delta: timedelta) -> None:
        time.sleep(delta.total_seconds() + 1)

    def receive_due(self) -> list[AnalysisJobMessage]:
        received = []
        with self._client.get_queue_receiver(self._queue_name) as receiver:
            while batch := receiver.receive_messages(
                max_message_count=20, max_wait_time=3
            ):
                for message in batch:
                    received.append(AnalysisJobMessage.from_json(message_body(message)))
                    receiver.complete_message(message)
        return received


class PostgresHarness:
    delay_unit = timedelta(seconds=1)

    def __init__(self, engine: Engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE analysis_job_queue"))
        sessions = sessionmaker(engine)
        self.publisher: JobPublisher = PostgresJobPublisher(sessions)
        self._receiver = PostgresQueueReceiver(sessions)

    def advance(self, delta: timedelta) -> None:
        time.sleep(delta.total_seconds() + 0.2)

    def receive_due(self) -> list[AnalysisJobMessage]:
        received = []
        while batch := self._receiver.receive_messages(max_message_count=20):
            for message in batch:
                received.append(AnalysisJobMessage.from_json(message.body))
                self._receiver.complete_message(message)
        return received


@pytest.fixture(
    params=[
        pytest.param("in-process", marks=pytest.mark.unit),
        pytest.param("service-bus-fake", marks=pytest.mark.unit),
        pytest.param("postgres", marks=pytest.mark.integration),
        pytest.param("service-bus", marks=pytest.mark.integration),
    ]
)
def harness(request: pytest.FixtureRequest) -> Iterator[PublisherHarness]:
    if request.param == "in-process":
        yield InProcessHarness()
    elif request.param == "service-bus-fake":
        yield FakeServiceBusHarness()
    elif request.param == "postgres":
        yield PostgresHarness(request.getfixturevalue("database_engine"))
    else:
        connection = os.environ.get(SERVICEBUS_ENV)
        if not connection:
            pytest.skip(f"{SERVICEBUS_ENV} is not set")
        queue_name = os.environ.get(
            "CODEBREAKERS_TEST_SERVICEBUS_QUEUE", "analysis-jobs-test"
        )
        with ServiceBusClient.from_connection_string(connection) as client:
            real = RealServiceBusHarness(client, queue_name)
            real.receive_due()  # start from an empty queue
            try:
                yield real
            finally:
                real.close()


def _message(n: int, attempt: int = 1) -> AnalysisJobMessage:
    return AnalysisJobMessage(
        job_id=UUID(int=n), attempt=attempt, trace=TraceContext(f"req-{n}", TRACEPARENT)
    )


def test_published_message_is_received_intact(harness: PublisherHarness) -> None:
    harness.publisher.publish(_message(1, attempt=3))
    assert harness.receive_due() == [_message(1, attempt=3)]


def test_immediate_messages_are_all_delivered(harness: PublisherHarness) -> None:
    for n in range(1, 4):
        harness.publisher.publish(_message(n))
    received = harness.receive_due()
    assert sorted(m.job_id.int for m in received) == [1, 2, 3]


def test_delayed_message_is_hidden_until_due(harness: PublisherHarness) -> None:
    harness.publisher.publish(_message(1), delay=harness.delay_unit)
    harness.publisher.publish(_message(2))

    assert harness.receive_due() == [_message(2)]
    harness.advance(harness.delay_unit)
    assert harness.receive_due() == [_message(1)]


def test_zero_or_negative_delay_publishes_immediately(
    harness: PublisherHarness,
) -> None:
    harness.publisher.publish(_message(1), delay=timedelta(0))
    harness.publisher.publish(_message(2), delay=timedelta(seconds=-5))
    assert sorted(m.job_id.int for m in harness.receive_due()) == [1, 2]
