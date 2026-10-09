"""Trace propagation, job metrics, and queue gauges."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from codebreakers.application.analysis import AnalysisJob, AnalysisStatus
from codebreakers.application.errors import AnalysisInterruptedError
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.application.processing import (
    AnalysisJobProcessor,
    ExecutionBudget,
    RetryPolicy,
)
from codebreakers.domain.cryptanalysis.models import AnalysisResult
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.infrastructure.structured_logging import ServiceIdentity
from codebreakers.infrastructure.telemetry import (
    ATTEMPT_ATTRIBUTE,
    CONNECTION_STRING_ENV,
    JOB_ID_ATTRIBUTE,
    OUTCOME_ATTRIBUTE,
    JobMetrics,
    QueueStats,
    TracingJobPublisher,
    configure_telemetry,
    current_traceparent,
    job_span,
    register_queue_gauges,
    trace_engine,
)
from codebreakers.worker.handler import INVALID_MESSAGE, JobMessageHandler

JOB_ID = UUID(int=11)
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
PARENT_ID = "b7ad6b7169203331"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_ID}-01"
SOURCE_TEXT = "WKHUH LV QR VHFUHW KHUH"
RESULT = AnalysisResult(
    analyzer="caesar-bruteforce",
    language="english",
    language_version="v1",
    candidates=(),
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[AnalysisJobMessage] = []

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        self.published.append(message)


class ScriptedExecutor:
    def __init__(self, outcome: AnalysisResult | Exception) -> None:
        self.outcome = outcome

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisResult:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _message(
    attempt: int = 1, traceparent: str | None = TRACEPARENT
) -> AnalysisJobMessage:
    return AnalysisJobMessage(
        job_id=JOB_ID, attempt=attempt, trace=TraceContext("req-7", traceparent)
    )


def _metrics() -> tuple[JobMetrics, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    return JobMetrics(meter), reader


def _points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
    points: dict[str, list[Any]] = {}
    if data is None:
        return points
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points[metric.name] = list(metric.data.data_points)
    return points


def _handler(
    outcome: AnalysisResult | Exception, metrics: JobMetrics, max_attempts: int = 3
) -> JobMessageHandler:
    repo = InMemoryAnalysisRepository()
    job = AnalysisJob(
        id=JOB_ID,
        analyzer="caesar-bruteforce",
        language="english",
        status=AnalysisStatus.PENDING,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=None,
        result=None,
    )
    repo.enqueue(job, SOURCE_TEXT, _message())
    processor = AnalysisJobProcessor(
        repo,
        ScriptedExecutor(outcome),
        ExecutionBudget(time_limit=timedelta(seconds=5)),
    )
    policy = RetryPolicy(max_attempts=max_attempts, base_delay=timedelta(seconds=1))
    return JobMessageHandler(processor, policy, metrics=metrics)


# --- Spans -------------------------------------------------------------------


@pytest.mark.unit
def test_job_span_continues_the_trace_carried_by_the_message(
    span_exporter: InMemorySpanExporter,
) -> None:
    with job_span("process analysis-job", SpanKind.CONSUMER, _message(2)):
        inner = current_traceparent()

    (span,) = span_exporter.get_finished_spans()
    assert f"{span.context.trace_id:032x}" == TRACE_ID
    assert span.parent is not None and f"{span.parent.span_id:016x}" == PARENT_ID
    assert span.kind is SpanKind.CONSUMER
    assert span.attributes is not None
    assert span.attributes[JOB_ID_ATTRIBUTE] == str(JOB_ID)
    assert span.attributes[ATTEMPT_ATTRIBUTE] == 2
    assert span.attributes["codebreakers.correlation_id"] == "req-7"
    assert inner == f"00-{TRACE_ID}-{span.context.span_id:016x}-01"


@pytest.mark.unit
def test_job_span_without_a_traceparent_starts_a_new_trace(
    span_exporter: InMemorySpanExporter,
) -> None:
    with job_span("process analysis-job", SpanKind.CONSUMER, _message(1, None)):
        pass

    (span,) = span_exporter.get_finished_spans()
    assert span.parent is None


@pytest.mark.unit
def test_job_span_failures_record_the_exception_type_only(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(ValueError, match="SECRET"):
        with job_span("process analysis-job", SpanKind.CONSUMER, _message()):
            raise ValueError("SECRET submitted text")

    (span,) = span_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError"
    assert span.events == ()


@pytest.mark.unit
def test_current_traceparent_is_none_outside_a_span() -> None:
    assert current_traceparent() is None


@pytest.mark.unit
def test_publisher_propagates_its_producer_span_to_the_message(
    span_exporter: InMemorySpanExporter,
) -> None:
    inner = RecordingPublisher()
    publisher = TracingJobPublisher(inner, "postgresql", "analysis_job_queue")

    publisher.publish(_message())

    (span,) = span_exporter.get_finished_spans()
    (sent,) = inner.published
    assert span.name == "send analysis_job_queue"
    assert span.kind is SpanKind.PRODUCER
    assert span.attributes is not None
    assert span.attributes["messaging.system"] == "postgresql"
    assert span.attributes["messaging.destination.name"] == "analysis_job_queue"
    assert sent.trace.traceparent == f"00-{TRACE_ID}-{span.context.span_id:016x}-01"
    assert sent.trace.correlation_id == "req-7"
    assert (sent.job_id, sent.attempt) == (JOB_ID, 1)


@pytest.mark.unit
def test_publisher_without_tracing_forwards_the_message_unchanged() -> None:
    inner = RecordingPublisher()
    message = _message()

    TracingJobPublisher(inner, "servicebus", "jobs").publish(message)

    assert inner.published == [message]


# --- Worker metrics and consumer spans ---------------------------------------


@pytest.mark.unit
def test_handler_records_a_consumer_span_and_success_metrics(
    span_exporter: InMemorySpanExporter,
) -> None:
    metrics, reader = _metrics()

    _handler(RESULT, metrics).handle(_message().to_json())

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "process analysis-job"
    assert span.attributes is not None
    assert span.attributes[OUTCOME_ATTRIBUTE] == "succeeded"
    points = _points(reader)
    (processed,) = points["codebreakers.jobs.processed"]
    assert processed.value == 1
    assert dict(processed.attributes) == {OUTCOME_ATTRIBUTE: "succeeded"}
    (duration,) = points["codebreakers.jobs.duration"]
    assert duration.count == 1
    assert "codebreakers.jobs.retries" not in points
    assert SOURCE_TEXT not in str(span.to_json())


@pytest.mark.unit
def test_handler_counts_retries_then_dead_letters() -> None:
    metrics, reader = _metrics()
    handler = _handler(
        AnalysisInterruptedError("analysis-interrupted", "child died"),
        metrics,
        max_attempts=2,
    )

    first = handler.handle(_message().to_json())
    assert first.message is not None
    handler.handle(first.message.to_json())

    points = _points(reader)
    assert [p.value for p in points["codebreakers.jobs.retries"]] == [1]
    (dead,) = points["codebreakers.jobs.dead_lettered"]
    assert dead.value == 1
    assert dict(dead.attributes) == {
        "codebreakers.dead_letter.reason": "retries-exhausted"
    }


@pytest.mark.unit
def test_handler_counts_poison_messages_as_dead_lettered() -> None:
    metrics, reader = _metrics()

    _handler(RESULT, metrics).handle(b"\x00not json")

    (dead,) = _points(reader)["codebreakers.jobs.dead_lettered"]
    assert dict(dead.attributes) == {"codebreakers.dead_letter.reason": INVALID_MESSAGE}


# --- Queue gauges ------------------------------------------------------------


@pytest.mark.unit
def test_queue_gauges_share_one_read_per_collection() -> None:
    reads: list[QueueStats] = []
    now = [0.0]

    def read() -> QueueStats:
        reads.append(QueueStats(depth=4, oldest_ready_age_seconds=42.5, dead_letters=1))
        return reads[-1]

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    register_queue_gauges(read, meter, max_age=10, clock=lambda: now[0])

    points = _points(reader)
    assert [p.value for p in points["codebreakers.queue.depth"]] == [4]
    assert [p.value for p in points["codebreakers.queue.oldest_age"]] == [42.5]
    assert [p.value for p in points["codebreakers.queue.dead_letters"]] == [1]
    assert len(reads) == 1

    now[0] = 11.0
    _points(reader)
    assert len(reads) == 2


@pytest.mark.unit
def test_queue_gauges_skip_a_sample_when_the_read_fails() -> None:
    def read() -> QueueStats:
        raise ConnectionError("database unavailable")

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    register_queue_gauges(read, meter)

    points = _points(reader)
    assert all(not values for values in points.values())


# --- Exporter configuration --------------------------------------------------


@pytest.mark.unit
def test_telemetry_names_the_service_role() -> None:
    identity = ServiceIdentity("worker", "dev", "0.1.0", "abc123")
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as configure:
        assert configure_telemetry({CONNECTION_STRING_ENV: "x"}, identity) is True

    attributes = dict(configure.call_args.kwargs["resource"].attributes)
    assert {
        "service.name": "codebreakers-worker",
        "service.namespace": "codebreakers",
        "service.version": "abc123",
        "deployment.environment.name": "dev",
    }.items() <= attributes.items()


# --- Database spans ----------------------------------------------------------


@pytest.mark.unit
def test_engine_spans_carry_parameterized_sql_but_never_values(
    span_exporter: InMemorySpanExporter,
) -> None:
    engine = create_engine("sqlite://")
    trace_engine(engine)

    with engine.connect() as connection:
        connection.execute(text("SELECT :value"), {"value": SOURCE_TEXT})
        with pytest.raises(OperationalError):
            connection.execute(text("SELECT * FROM missing WHERE x = :v"), {"v": "z"})

    ok, failed = span_exporter.get_finished_spans()
    assert (ok.name, ok.kind) == ("SELECT", SpanKind.CLIENT)
    assert ok.attributes is not None
    assert ok.attributes["db.system"] == "sqlite"
    assert ok.attributes["db.statement"] == "SELECT ?"
    assert SOURCE_TEXT not in ok.to_json()
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.status.description == "OperationalError"
    assert failed.events == ()
