"""Queue message schema and publishing ports for asynchronous analysis jobs."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Protocol, Self
from uuid import UUID

from codebreakers.application.errors import InvalidJobMessageError

ANALYSIS_JOB_MESSAGE_SCHEMA_VERSION = 1

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TRACEPARENT = re.compile(
    r"^(?!ff-)[0-9a-f]{2}-(?!0{32}-)[0-9a-f]{32}-(?!0{16}-)"
    r"[0-9a-f]{16}-[0-9a-f]{2}$"
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Correlation and W3C trace context carried from a request to a worker."""

    correlation_id: str | None = None
    traceparent: str | None = None

    @classmethod
    def sanitized(cls, correlation_id: str | None, traceparent: str | None) -> Self:
        """Keep only well-formed identifiers so untrusted values never propagate."""
        return cls(
            correlation_id=(
                correlation_id
                if correlation_id is not None
                and _CORRELATION_ID.fullmatch(correlation_id)
                else None
            ),
            traceparent=(
                traceparent
                if traceparent is not None and _TRACEPARENT.fullmatch(traceparent)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AnalysisJobMessage:
    """Versioned queue message identifying a job; it never carries source text."""

    job_id: UUID
    attempt: int = 1
    trace: TraceContext = field(default_factory=TraceContext)
    schema_version: int = ANALYSIS_JOB_MESSAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.attempt < 1:
            msg = "attempt must be at least 1."
            raise ValueError(msg)

    def next_attempt(self) -> Self:
        """Return the message to publish when retrying after a failure."""
        return replace(self, attempt=self.attempt + 1)

    def to_json(self) -> str:
        """Serialize the message as compact JSON."""
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "job_id": str(self.job_id),
                "attempt": self.attempt,
                "correlation_id": self.trace.correlation_id,
                "traceparent": self.trace.traceparent,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> Self:
        """Parse and validate a message, rejecting unknown schema versions."""
        try:
            data: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise InvalidJobMessageError("Job message is not valid JSON.") from err
        if not isinstance(data, dict):
            raise InvalidJobMessageError("Job message must be a JSON object.")
        version = data.get("schema_version")
        if version != ANALYSIS_JOB_MESSAGE_SCHEMA_VERSION:
            raise InvalidJobMessageError(
                f"Unsupported job message schema version: {version!r}."
            )
        attempt = data.get("attempt")
        if type(attempt) is not int or attempt < 1:
            raise InvalidJobMessageError("Job message attempt must be a positive int.")
        try:
            job_id = UUID(str(data.get("job_id")))
        except ValueError as err:
            raise InvalidJobMessageError("Job message job_id must be a UUID.") from err
        correlation_id = data.get("correlation_id")
        traceparent = data.get("traceparent")
        return cls(
            job_id=job_id,
            attempt=attempt,
            trace=TraceContext.sanitized(
                correlation_id if isinstance(correlation_id, str) else None,
                traceparent if isinstance(traceparent, str) else None,
            ),
        )


class JobPublisher(Protocol):
    """Port for handing job messages to a queue."""

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        """Publish a message, optionally making it visible only after a delay."""
        ...


class AnalysisOutbox(Protocol):
    """Port for job messages committed with their job and not yet published."""

    def relay(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish up to ``limit`` entries and remove each one once published.

        Stops at the first publishing failure and re-raises it, leaving that
        entry and any later ones for the next attempt. Delivery is therefore
        at least once, and consumers must tolerate duplicates.
        """
        ...

    def recover(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish a fresh message for unfinished jobs without an outbox entry.

        Used when a non-durable queue may have lost messages that were already
        relayed, for example after an in-process runtime restarts. Duplicates
        are harmless because workers are idempotent.
        """
        ...
