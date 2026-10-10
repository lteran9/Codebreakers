"""PostgreSQL job queue: a local, Service Bus-like substitute for Compose.

The queue reproduces the broker semantics the worker relies on: scheduled
delivery, peek-lock receipt with lock tokens, abandon, a maximum delivery
count, and a dead-letter sub-queue. Rows are locked with ``FOR UPDATE SKIP
LOCKED`` only while being claimed, so analyses never hold a transaction open.
All times come from the database clock, so containers never disagree.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Delete, Update, delete, func, insert, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from codebreakers.application.messaging import AnalysisJobMessage, JobPublisher
from codebreakers.infrastructure.messaging.settlement import (
    DeadLetterReport,
    DeadLetterSummary,
    settle_dead_letters,
    summarize_dead_letter,
)
from codebreakers.infrastructure.persistence.postgres import JobQueueRecord
from codebreakers.infrastructure.telemetry import JOB_METRICS, QueueStats

logger = logging.getLogger("codebreakers.worker")

MAX_DELIVERY_COUNT_EXCEEDED = "MaxDeliveryCountExceeded"
DEFAULT_MAX_DELIVERY_COUNT = 10
DEFAULT_LOCK_DURATION = timedelta(minutes=1)
_REASON_LENGTH = 128


class MessageLockLostError(RuntimeError):
    """Raised when settling a message whose lock another receiver now holds."""


@dataclass(frozen=True, slots=True)
class PostgresQueueMessage:
    """A queue row handed to a receiver, identified by its lock token."""

    sequence_number: int
    body: str
    enqueued_time_utc: datetime
    delivery_count: int
    lock_token: UUID | None = None
    dead_letter_reason: str | None = None
    dead_letter_error_description: str | None = None


def _to_message(
    record: JobQueueRecord, lock_token: UUID | None
) -> PostgresQueueMessage:
    return PostgresQueueMessage(
        sequence_number=record.id,
        body=record.payload,
        enqueued_time_utc=record.enqueued_at,
        delivery_count=record.delivery_count,
        lock_token=lock_token,
        dead_letter_reason=record.dead_letter_reason,
        dead_letter_error_description=record.dead_letter_description,
    )


class PostgresJobPublisher(JobPublisher):
    """Insert job messages, delaying visibility for scheduled retries."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        """Enqueue a message that becomes receivable after ``delay``."""
        with self._sessions.begin() as session:
            session.execute(
                insert(JobQueueRecord).values(
                    payload=message.to_json(),
                    enqueued_at=func.now(),
                    available_at=func.now() + max(delay, timedelta(0)),
                )
            )


class PostgresQueueReceiver:
    """Peek-lock receiver over the main queue or, optionally, its dead letters.

    Implements the ``MessageSettler`` protocol. Each receipt locks a message
    for ``lock_duration`` under a fresh token; settling requires that token,
    so a receiver whose lock lapsed and was taken over cannot settle twice.
    A message already delivered ``max_delivery_count`` times is dead-lettered
    instead of being delivered again, mirroring Service Bus.
    """

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        dead_letter: bool = False,
        lock_duration: timedelta = DEFAULT_LOCK_DURATION,
        max_delivery_count: int = DEFAULT_MAX_DELIVERY_COUNT,
        poll_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if lock_duration <= timedelta(0) or max_delivery_count < 1:
            msg = "lock_duration and max_delivery_count must be positive."
            raise ValueError(msg)
        self._sessions = sessions
        self._dead_letter = dead_letter
        self._lock_duration = lock_duration
        self._max_delivery_count = max_delivery_count
        self._poll_interval = poll_interval
        self._sleep = sleep

    def receive_messages(
        self, max_message_count: int | None = 1, max_wait_time: float | None = None
    ) -> Sequence[PostgresQueueMessage]:
        """Lock up to ``max_message_count`` messages, polling the main queue.

        Dead letters never become due later, so that sub-queue is not polled.
        """
        limit = max(max_message_count or 1, 1)
        deadline = time.monotonic() + (max_wait_time or 0.0)
        while True:
            messages = self._lock_batch(limit)
            remaining = deadline - time.monotonic()
            if messages or self._dead_letter or remaining <= 0:
                return messages
            self._sleep(min(self._poll_interval, remaining))

    def complete_message(self, message: PostgresQueueMessage, /) -> None:
        """Delete a locked message."""
        self._settle(message, delete(JobQueueRecord))

    def abandon_message(self, message: PostgresQueueMessage, /) -> None:
        """Release a lock so the message can be received again immediately."""
        self._settle(
            message,
            sql_update(JobQueueRecord).values(lock_token=None, locked_until=None),
        )

    def dead_letter_message(
        self,
        message: PostgresQueueMessage,
        /,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None:
        """Move a locked message to the dead-letter sub-queue."""
        self._settle(
            message,
            sql_update(JobQueueRecord).values(
                lock_token=None,
                locked_until=None,
                dead_lettered_at=func.now(),
                dead_letter_reason=None if reason is None else reason[:_REASON_LENGTH],
                dead_letter_description=error_description,
            ),
        )

    def _sub_queue(self) -> ColumnElement[bool]:
        if self._dead_letter:
            return JobQueueRecord.dead_lettered_at.is_not(None)
        return JobQueueRecord.dead_lettered_at.is_(None)

    def _lock_batch(self, limit: int) -> list[PostgresQueueMessage]:
        unlocked = or_(
            JobQueueRecord.locked_until.is_(None),
            JobQueueRecord.locked_until <= func.now(),
        )
        query = select(JobQueueRecord).where(self._sub_queue(), unlocked)
        if self._dead_letter:
            query = query.order_by(JobQueueRecord.id)
        else:
            query = query.where(JobQueueRecord.available_at <= func.now()).order_by(
                JobQueueRecord.available_at, JobQueueRecord.id
            )
        messages: list[PostgresQueueMessage] = []
        with self._sessions.begin() as session:
            now = session.scalar(select(func.now()))
            assert now is not None
            for record in session.scalars(
                query.limit(limit).with_for_update(skip_locked=True)
            ):
                if (
                    not self._dead_letter
                    and record.delivery_count >= self._max_delivery_count
                ):
                    self._dead_letter_exhausted(record, now)
                    continue
                token = uuid4()
                record.lock_token = token
                record.locked_until = now + self._lock_duration
                if not self._dead_letter:
                    record.delivery_count += 1
                messages.append(_to_message(record, token))
        return messages

    def _dead_letter_exhausted(self, record: JobQueueRecord, now: datetime) -> None:
        record.lock_token = None
        record.locked_until = None
        record.dead_lettered_at = now
        record.dead_letter_reason = MAX_DELIVERY_COUNT_EXCEEDED
        record.dead_letter_description = (
            f"Message was delivered {record.delivery_count} times without settling."
        )
        JOB_METRICS.dead_lettered(MAX_DELIVERY_COUNT_EXCEEDED)
        logger.warning(
            "job_message_dead_lettered reason=%s sequence_number=%d",
            MAX_DELIVERY_COUNT_EXCEEDED,
            record.id,
        )

    def _settle(
        self,
        message: PostgresQueueMessage,
        statement: Delete | Update,
    ) -> None:
        if message.lock_token is None:
            msg = "Only messages received with a lock can be settled."
            raise MessageLockLostError(msg)
        with self._sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    statement.where(
                        JobQueueRecord.id == message.sequence_number,
                        JobQueueRecord.lock_token == message.lock_token,
                        self._sub_queue(),
                    )
                ),
            )
            if result.rowcount != 1:
                msg = f"Lock lost for queue message {message.sequence_number}."
                raise MessageLockLostError(msg)


class PostgresDeadLetterQueue:
    """Operate on dead-lettered rows of the PostgreSQL job queue."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list(self, max_count: int) -> list[DeadLetterSummary]:
        """Read dead letters oldest first without locking or removing them."""
        with self._sessions() as session:
            records = session.scalars(
                select(JobQueueRecord)
                .where(JobQueueRecord.dead_lettered_at.is_not(None))
                .order_by(JobQueueRecord.id)
                .limit(max_count)
            )
            return [summarize_dead_letter(_to_message(r, None)) for r in records]

    def replay(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Republish selected dead letters to the main queue and remove them."""
        return settle_dead_letters(
            PostgresQueueReceiver(self._sessions, dead_letter=True),
            sequence_numbers=sequence_numbers,
            max_count=max_count,
            replay_to=PostgresJobPublisher(self._sessions),
            max_wait_time=0.0,
        )

    def discard(
        self, sequence_numbers: frozenset[int] | None, max_count: int
    ) -> DeadLetterReport:
        """Remove selected dead letters permanently."""
        return settle_dead_letters(
            PostgresQueueReceiver(self._sessions, dead_letter=True),
            sequence_numbers=sequence_numbers,
            max_count=max_count,
            replay_to=None,
            max_wait_time=0.0,
        )


class PostgresQueueStats:
    """Read backlog figures for the queue gauges with one aggregate query."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def read(self) -> QueueStats:
        """Return live depth, oldest ready age, and dead-letter count."""
        live = JobQueueRecord.dead_lettered_at.is_(None)
        ready = live & (JobQueueRecord.available_at <= func.now())
        query = select(
            func.count().filter(live),
            func.coalesce(
                func.extract(
                    "epoch",
                    func.now() - func.min(JobQueueRecord.available_at).filter(ready),
                ),
                0,
            ),
            func.count().filter(JobQueueRecord.dead_lettered_at.is_not(None)),
        )
        with self._sessions() as session:
            depth, oldest, dead = session.execute(query).one()
        return QueueStats(int(depth), float(oldest), int(dead))
