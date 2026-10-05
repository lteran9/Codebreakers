"""Broker-neutral settlement of job deliveries and dead-letter operations.

Every queue adapter exposes received messages and a settler with peek-lock
semantics (complete, abandon, dead-letter), so the same consumer loop and
dead-letter tooling run against Azure Service Bus and the PostgreSQL queue.
"""

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from codebreakers.application.errors import InvalidJobMessageError
from codebreakers.application.messaging import AnalysisJobMessage, JobPublisher
from codebreakers.application.processing import Action, Decision

logger = logging.getLogger("codebreakers.worker")


class ReceivedMessage(Protocol):
    """Locked delivery as seen by the consumer and dead-letter tooling."""

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
    """Peek-lock receiver that settles the messages it hands out."""

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


class JobConsumer:
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

        If settlement fails the delivery is abandoned, so the queue redelivers
        it and eventually dead-letters it after its maximum delivery count.
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
        except Exception as err:  # boundary: hand the delivery back to the queue
            logger.error(
                "job_settlement_error error=%s sequence_number=%s",
                type(err).__name__,
                message.sequence_number,
            )
            try:
                receiver.abandon_message(message)
            except Exception as abandon_err:  # lock expiry also redelivers it
                logger.error("job_abandon_error error=%s", type(abandon_err).__name__)

    def consume[M: ReceivedMessage](
        self,
        receiver: MessageSettler[M],
        stop: threading.Event,
        max_wait_time: float = 5.0,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        """Settle one message at a time until ``stop`` is set.

        ``heartbeat`` runs once per receive cycle so a liveness probe can tell
        a busy consumer from a stuck one.
        """
        while not stop.is_set():
            if heartbeat is not None:
                heartbeat()
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


class DeadLetterQueue(Protocol):
    """Operator actions on the dead letters of the analysis job queue."""

    def list(self, max_count: int) -> list[DeadLetterSummary]:
        """Return dead-letter metadata without removing or locking messages."""
        ...

    def replay(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Republish selected dead letters as first attempts and remove them."""
        ...

    def discard(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Remove selected dead letters permanently."""
        ...


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
