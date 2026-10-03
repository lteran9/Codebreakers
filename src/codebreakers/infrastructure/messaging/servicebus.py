"""Azure Service Bus adapters for publishing and consuming analysis jobs."""

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from azure.servicebus import (
    AutoLockRenewer,
    ServiceBusClient,
    ServiceBusMessage,
    ServiceBusSubQueue,
)

from codebreakers.application.errors import InvalidJobMessageError
from codebreakers.application.messaging import AnalysisJobMessage, JobPublisher
from codebreakers.application.processing import Action, Decision

logger = logging.getLogger("codebreakers.worker")

MESSAGE_SUBJECT = "analysis-job"


class MessageSender(Protocol):
    """Subset of ``ServiceBusSender`` used by the publisher."""

    def send_messages(self, message: ServiceBusMessage, /) -> None: ...

    def schedule_messages(
        self, messages: ServiceBusMessage, schedule_time_utc: datetime, /
    ) -> list[int]: ...


class ReceivedMessage(Protocol):
    """Subset of ``ServiceBusReceivedMessage`` read by the adapters."""

    @property
    def body(self) -> Any: ...

    @property
    def sequence_number(self) -> int | None: ...

    @property
    def enqueued_time_utc(self) -> datetime | None: ...

    @property
    def delivery_count(self) -> int | None: ...

    @property
    def dead_letter_reason(self) -> str | None: ...

    @property
    def dead_letter_error_description(self) -> str | None: ...


class MessageSettler[M](Protocol):
    """Subset of ``ServiceBusReceiver`` used to settle received messages."""

    def receive_messages(
        self, max_message_count: int | None = 1, max_wait_time: float | None = None
    ) -> Sequence[M]: ...

    def complete_message(self, message: M, /) -> None: ...

    def abandon_message(self, message: M, /) -> None: ...

    def dead_letter_message(
        self,
        message: M,
        /,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None: ...


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


def message_body(message: ReceivedMessage) -> bytes:
    """Return the raw body of a received data message."""
    body = message.body
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode()
    if isinstance(body, Iterable):
        return b"".join(
            part if isinstance(part, bytes) else str(part).encode() for part in body
        )
    return str(body).encode()


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


class ServiceBusJobConsumer:
    """Receive job messages and settle them according to handler decisions."""

    def __init__(
        self,
        handle: Callable[[bytes], Decision],
        publisher: JobPublisher,
    ) -> None:
        self._handle = handle
        self._publisher = publisher

    def settle[M: ReceivedMessage](
        self, receiver: MessageSettler[M], message: M
    ) -> None:
        """Process one delivery and complete, reschedule, or dead-letter it.

        If settlement fails the delivery is abandoned, so the broker redelivers
        it and eventually dead-letters it after ``MaxDeliveryCount`` attempts.
        """
        try:
            decision = self._handle(message_body(message))
            if decision.action is Action.RETRY:
                assert decision.message is not None
                self._publisher.publish(decision.message, decision.delay)
                receiver.complete_message(message)
            elif decision.action is Action.DEAD_LETTER:
                receiver.dead_letter_message(
                    message,
                    reason=decision.reason,
                    error_description=decision.description,
                )
            else:
                receiver.complete_message(message)
        except Exception as err:  # boundary: hand the delivery back to the broker
            logger.error(
                "job_settlement_error error=%s sequence_number=%s",
                type(err).__name__,
                message.sequence_number,
            )
            try:
                receiver.abandon_message(message)
            except Exception as abandon_err:  # lock expiry also redelivers it
                logger.error("job_abandon_error error=%s", type(abandon_err).__name__)

    def run(
        self,
        client: ServiceBusClient,
        queue_name: str,
        stop: threading.Event,
        lock_renewal: timedelta,
        max_wait_time: float = 5.0,
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
            while not stop.is_set():
                for message in receiver.receive_messages(
                    max_message_count=1, max_wait_time=max_wait_time
                ):
                    self.settle(receiver, message)


@dataclass(frozen=True, slots=True)
class DeadLetterSummary:
    """Metadata about a dead-lettered message; it never includes job input."""

    sequence_number: int | None
    enqueued_at: datetime | None
    delivery_count: int | None
    reason: str | None
    description: str | None
    job_id: str | None
    attempt: int | None


def summarize_dead_letter(message: ReceivedMessage) -> DeadLetterSummary:
    """Describe a dead-lettered message without exposing unparsed payloads."""
    try:
        decoded: AnalysisJobMessage | None = AnalysisJobMessage.from_json(
            message_body(message)
        )
    except InvalidJobMessageError:
        decoded = None
    return DeadLetterSummary(
        sequence_number=message.sequence_number,
        enqueued_at=message.enqueued_time_utc,
        delivery_count=message.delivery_count,
        reason=message.dead_letter_reason,
        description=message.dead_letter_error_description,
        job_id=None if decoded is None else str(decoded.job_id),
        attempt=None if decoded is None else decoded.attempt,
    )


@dataclass(frozen=True, slots=True)
class DeadLetterReport:
    """Counts from a replay or discard run."""

    settled: int
    skipped: int


def settle_dead_letters[M: ReceivedMessage](
    receiver: MessageSettler[M],
    *,
    sequence_numbers: frozenset[int] | None,
    max_count: int,
    replay_to: JobPublisher | None,
    max_wait_time: float = 5.0,
    max_scan: int = 1000,
) -> DeadLetterReport:
    """Replay or discard selected dead letters; ``None`` selects every message.

    Replayed messages are republished as a fresh first attempt so the retry
    budget restarts. Messages that are not selected, or cannot be decoded for
    replay, stay locked until the scan ends so the receiver keeps advancing
    through the queue; they are then abandoned. Undecodable messages count as
    skipped and must be discarded explicitly.
    """
    settled = skipped = scanned = 0
    held: list[M] = []
    seen: set[int | None] = set()
    try:
        while settled < max_count and scanned < max_scan:
            if sequence_numbers is not None and sequence_numbers <= seen:
                break
            batch = receiver.receive_messages(
                max_message_count=min(max_scan - scanned, 50),
                max_wait_time=max_wait_time,
            )
            if not batch:
                break
            for message in batch:
                scanned += 1
                redelivered = message.sequence_number in seen
                seen.add(message.sequence_number)
                selected = sequence_numbers is None or (
                    message.sequence_number in sequence_numbers
                )
                if redelivered or not selected or settled >= max_count:
                    held.append(message)
                    continue
                if replay_to is not None:
                    try:
                        decoded = AnalysisJobMessage.from_json(message_body(message))
                    except InvalidJobMessageError:
                        held.append(message)
                        skipped += 1
                        continue
                    replay_to.publish(
                        AnalysisJobMessage(job_id=decoded.job_id, trace=decoded.trace)
                    )
                receiver.complete_message(message)
                settled += 1
    finally:
        for message in held:
            try:
                receiver.abandon_message(message)
            except Exception as err:  # the lock expires and releases it anyway
                logger.warning("dead_letter_abandon_error error=%s", type(err).__name__)
    return DeadLetterReport(settled=settled, skipped=skipped)


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
