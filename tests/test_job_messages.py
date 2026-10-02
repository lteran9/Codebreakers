"""Tests for the versioned job message schema and trace context."""

import json
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codebreakers.application.errors import InvalidJobMessageError
from codebreakers.application.messaging import (
    ANALYSIS_JOB_MESSAGE_SCHEMA_VERSION,
    AnalysisJobMessage,
    TraceContext,
)

JOB_ID = UUID("0b8f6a3e-2d43-4c1e-9f55-6a1d2b7c9e10")
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


@pytest.mark.unit
def test_message_serializes_only_identifiers_and_trace_context() -> None:
    message = AnalysisJobMessage(
        job_id=JOB_ID, attempt=2, trace=TraceContext("req-1", TRACEPARENT)
    )
    assert json.loads(message.to_json()) == {
        "schema_version": ANALYSIS_JOB_MESSAGE_SCHEMA_VERSION,
        "job_id": str(JOB_ID),
        "attempt": 2,
        "correlation_id": "req-1",
        "traceparent": TRACEPARENT,
    }


@pytest.mark.unit
@given(
    job_id=st.uuids(),
    attempt=st.integers(min_value=1, max_value=1_000),
    correlation_id=st.one_of(
        st.none(), st.from_regex(r"\A[a-z0-9][a-z0-9._-]{0,20}\Z")
    ),
)
def test_message_round_trips(
    job_id: UUID, attempt: int, correlation_id: str | None
) -> None:
    message = AnalysisJobMessage(
        job_id=job_id, attempt=attempt, trace=TraceContext(correlation_id, TRACEPARENT)
    )
    assert AnalysisJobMessage.from_json(message.to_json()) == message
    assert AnalysisJobMessage.from_json(message.to_json().encode()) == message


@pytest.mark.unit
def test_next_attempt_increments_only_the_attempt() -> None:
    message = AnalysisJobMessage(job_id=JOB_ID, trace=TraceContext("req-1"))
    retried = message.next_attempt()
    assert retried.attempt == 2
    assert (retried.job_id, retried.trace) == (message.job_id, message.trace)


@pytest.mark.unit
def test_attempt_must_be_positive() -> None:
    with pytest.raises(ValueError, match="attempt"):
        AnalysisJobMessage(job_id=JOB_ID, attempt=0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("not json", "not valid JSON"),
        (b"\xff\xfe", "not valid JSON"),
        ("[]", "JSON object"),
        ('{"job_id": "x", "attempt": 1}', "schema version"),
        ('{"schema_version": 2, "job_id": "x", "attempt": 1}', "schema version"),
        ('{"schema_version": 1, "job_id": "nope", "attempt": 1}', "UUID"),
        (f'{{"schema_version": 1, "job_id": "{JOB_ID}", "attempt": 0}}', "attempt"),
        (f'{{"schema_version": 1, "job_id": "{JOB_ID}", "attempt": "1"}}', "attempt"),
        (f'{{"schema_version": 1, "job_id": "{JOB_ID}", "attempt": true}}', "attempt"),
    ],
)
def test_invalid_messages_are_rejected(raw: str | bytes, match: str) -> None:
    with pytest.raises(InvalidJobMessageError, match=match):
        AnalysisJobMessage.from_json(raw)


@pytest.mark.unit
def test_untrusted_trace_values_are_dropped_when_decoding() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "job_id": str(JOB_ID),
            "attempt": 1,
            "correlation_id": "bad id with spaces",
            "traceparent": 42,
        }
    )
    assert AnalysisJobMessage.from_json(raw).trace == TraceContext()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("correlation_id", "traceparent", "expected"),
    [
        ("req-1", TRACEPARENT, TraceContext("req-1", TRACEPARENT)),
        (None, None, TraceContext()),
        ("x" * 129, TRACEPARENT.upper(), TraceContext()),
        ("-leading", "00-abc-def-01", TraceContext()),
    ],
)
def test_trace_context_keeps_only_well_formed_values(
    correlation_id: str | None, traceparent: str | None, expected: TraceContext
) -> None:
    assert TraceContext.sanitized(correlation_id, traceparent) == expected
