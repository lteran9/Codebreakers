"""Azure Service Bus adapters for publishing and consuming analysis jobs."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from azure.servicebus import (
    AutoLockRenewer,
    ServiceBusClient,
    ServiceBusMessage,
    ServiceBusSubQueue,
)

from codebreakers.application.messaging import AnalysisJobMessage, JobPublisher
from codebreakers.infrastructure.messaging.settlement import (
    DeadLetterReport,
    DeadLetterSummary,
    JobConsumer,
    settle_dead_letters,
    summarize_dead_letter,
)

MESSAGE_SUBJECT = "analysis-job"


class MessageSender(Protocol):
    """Subset of ``ServiceBusSender`` used by the publisher."""

    def send_messages(self, message: ServiceBusMessage, /) -> None: ...

    def schedule_messages(
        self, messages: ServiceBusMessage, schedule_time_utc: datetime, /
    ) -> list[int]: ...


def to_service_bus_message(message: AnalysisJobMessage) -> ServiceBusMessage:
    """Build a broker message; trace context is mirrored into properties."""
    properties: dict[str | bytes, Any] = {
        "schema_version": message.schema_version,
        "job_id": str(message.job_id),
        "attempt": message.attempt,
    }
    if message.trace.traceparent is not None:
        properties["traceparent"] = message.trace.traceparent
    return ServiceBusMessage(
        message.to_json(),
        content_type="application/json",
        subject=MESSAGE_SUBJECT,
        correlation_id=message.trace.correlation_id,
        application_properties=properties,
    )


class ServiceBusJobPublisher(JobPublisher):
    """Publish job messages, scheduling delayed ones for later delivery."""

    def __init__(
        self,
        sender: MessageSender,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sender = sender
        self._clock = clock

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        """Send now, or schedule the message when a positive delay is given."""
        outgoing = to_service_bus_message(message)
        if delay > timedelta(0):
            self._sender.schedule_messages(outgoing, self._clock() + delay)
        else:
            self._sender.send_messages(outgoing)


class ServiceBusJobConsumer(JobConsumer):
    """Consume a Service Bus queue with automatic message lock renewal."""

    def run(
        self,
        client: ServiceBusClient,
        queue_name: str,
        stop: threading.Event,
        lock_renewal: timedelta,
        max_wait_time: float = 5.0,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        """Consume one message at a time until ``stop`` is set."""
        with (
            AutoLockRenewer(
                max_lock_renewal_duration=lock_renewal.total_seconds()
            ) as renewer,
            client.get_queue_receiver(
                queue_name, auto_lock_renewer=renewer, prefetch_count=0
            ) as receiver,
        ):
            self.consume(receiver, stop, max_wait_time, heartbeat)


class ServiceBusDeadLetterQueue:
    """Operate on the dead-letter sub-queue of the analysis job queue."""

    def __init__(self, client: ServiceBusClient, queue_name: str) -> None:
        self._client = client
        self._queue_name = queue_name

    def list(self, max_count: int) -> list[DeadLetterSummary]:
        """Peek at dead letters without locking or removing them."""
        with self._client.get_queue_receiver(
            self._queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER
        ) as receiver:
            return [
                summarize_dead_letter(message)
                for message in receiver.peek_messages(max_message_count=max_count)
            ]

    def replay(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Republish selected dead letters to the main queue and remove them."""
        with (
            self._client.get_queue_sender(self._queue_name) as sender,
            self._client.get_queue_receiver(
                self._queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER
            ) as receiver,
        ):
            return settle_dead_letters(
                receiver,
                sequence_numbers=sequence_numbers,
                max_count=max_count,
                replay_to=ServiceBusJobPublisher(sender),
            )

    def discard(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Remove selected dead letters permanently."""
        with self._client.get_queue_receiver(
            self._queue_name, sub_queue=ServiceBusSubQueue.DEAD_LETTER
        ) as receiver:
            return settle_dead_letters(
                receiver,
                sequence_numbers=sequence_numbers,
                max_count=max_count,
                replay_to=None,
            )
