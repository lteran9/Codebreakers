"""OpenTelemetry traces, metrics, and Azure Monitor export.

Nothing is exported unless ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, so
local runs, tests, and Compose never send telemetry. When ``AZURE_CLIENT_ID``
is also set, the exporter authenticates with that user-assigned managed
identity, which lets Application Insights reject key-only ingestion.

The span and metric helpers use the OpenTelemetry API only. Without a
configured SDK they are no-ops, so callers never need to check whether
telemetry is enabled.

Telemetry carries identifiers (job ID, correlation ID, outcome), never
submitted text, keys, or results. Exceptions are recorded by type only,
because their messages can echo user input.
"""

import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.metrics import CallbackOptions, Meter, Observation
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import Engine, event

from codebreakers.application.messaging import (
    AnalysisJobMessage,
    JobPublisher,
    TraceContext,
)
from codebreakers.infrastructure.structured_logging import ServiceIdentity

CONNECTION_STRING_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
CLIENT_ID_ENV = "AZURE_CLIENT_ID"
# Only application loggers are exported; SDK and server loggers stay local.
LOGGER_NAME = "codebreakers"
INSTRUMENTATION_NAME = "codebreakers"

JOB_ID_ATTRIBUTE = "codebreakers.job.id"
ATTEMPT_ATTRIBUTE = "codebreakers.job.attempt"
OUTCOME_ATTRIBUTE = "codebreakers.job.outcome"

_PROPAGATOR = TraceContextTextMapPropagator()


def configure_telemetry(
    env: Mapping[str, str] | None = None,
    identity: ServiceIdentity | None = None,
) -> bool:
    """Export traces, metrics, and ``codebreakers`` logs when configured.

    Call before the FastAPI application is created so it is instrumented.
    ``identity`` names the role (``codebreakers-api``, ``-worker``, ``-relay``)
    so Application Insights separates them. Returns whether telemetry was
    enabled.
    """
    source = os.environ if env is None else env
    connection_string = source.get(CONNECTION_STRING_ENV)
    if not connection_string:
        return False

    # Deferred so processes without telemetry never load the SDK.
    from azure.identity import ManagedIdentityCredential
    from azure.monitor.opentelemetry import configure_azure_monitor

    options: dict[str, Any] = {
        "connection_string": connection_string,
        "logger_name": LOGGER_NAME,
        "enable_live_metrics": False,
    }
    client_id = source.get(CLIENT_ID_ENV)
    if client_id:
        options["credential"] = ManagedIdentityCredential(client_id=client_id)
    if identity is not None:
        from opentelemetry.sdk.resources import Resource

        options["resource"] = Resource.create(
            {
                "service.name": f"codebreakers-{identity.service}",
                "service.namespace": "codebreakers",
                "service.version": identity.revision,
                "deployment.environment.name": identity.environment,
            }
        )
    configure_azure_monitor(**options)
    return True


def current_traceparent() -> str | None:
    """Return the W3C ``traceparent`` of the active span, if there is one."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def _parent_context(trace_context: TraceContext) -> Context | None:
    if trace_context.traceparent is None:
        return None
    return _PROPAGATOR.extract({"traceparent": trace_context.traceparent})


@contextmanager
def job_span(
    name: str,
    kind: SpanKind,
    message: AnalysisJobMessage,
    attributes: Mapping[str, str | int] | None = None,
) -> Iterator[Span]:
    """Start a span parented on the trace context carried by ``message``.

    Failures are marked with the exception type only; exception messages and
    events are not recorded because they can contain submitted text.
    """
    span_attributes: dict[str, str | int] = {
        JOB_ID_ATTRIBUTE: str(message.job_id),
        ATTEMPT_ATTRIBUTE: message.attempt,
    }
    if message.trace.correlation_id is not None:
        span_attributes["codebreakers.correlation_id"] = message.trace.correlation_id
    span_attributes.update(attributes or {})
    with trace.get_tracer(INSTRUMENTATION_NAME).start_as_current_span(
        name,
        context=_parent_context(message.trace),
        kind=kind,
        attributes=span_attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as err:
            span.set_status(Status(StatusCode.ERROR, type(err).__name__))
            raise


class TracingJobPublisher(JobPublisher):
    """Wrap a publisher in a producer span and propagate that span's context.

    The worker's consumer span then becomes a child of the publish, so one
    trace runs from the HTTP request through the outbox relay to the worker.
    """

    def __init__(self, inner: JobPublisher, system: str, destination: str) -> None:
        self._inner = inner
        self._system = system
        self._destination = destination

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        """Publish ``message`` inside a ``send`` span."""
        with job_span(
            f"send {self._destination}",
            SpanKind.PRODUCER,
            message,
            {
                "messaging.system": self._system,
                "messaging.destination.name": self._destination,
            },
        ) as span:
            outgoing = message
            if span.is_recording():
                outgoing = replace(
                    message,
                    trace=replace(message.trace, traceparent=current_traceparent()),
                )
            self._inner.publish(outgoing, delay)


class JobMetrics:
    """Worker throughput, duration, failure, retry, and dead-letter metrics."""

    def __init__(self, meter: Meter | None = None) -> None:
        active = meter or metrics.get_meter(INSTRUMENTATION_NAME)
        self._processed = active.create_counter(
            "codebreakers.jobs.processed",
            unit="{job}",
            description="Job deliveries handled, by outcome.",
        )
        self._duration = active.create_histogram(
            "codebreakers.jobs.duration",
            unit="s",
            description="Time spent handling one job delivery, by outcome.",
        )
        self._retries = active.create_counter(
            "codebreakers.jobs.retries",
            unit="{job}",
            description="Deliveries rescheduled after a transient failure.",
        )
        self._dead_lettered = active.create_counter(
            "codebreakers.jobs.dead_lettered",
            unit="{message}",
            description="Messages moved to the dead-letter queue, by reason.",
        )

    def processed(self, outcome: str, seconds: float) -> None:
        """Record one handled delivery and how long it took."""
        attributes = {OUTCOME_ATTRIBUTE: outcome}
        self._processed.add(1, attributes)
        self._duration.record(seconds, attributes)

    def retried(self) -> None:
        """Record a delivery rescheduled for another attempt."""
        self._retries.add(1)

    def dead_lettered(self, reason: str) -> None:
        """Record a message moved to the dead-letter queue."""
        self._dead_lettered.add(1, {"codebreakers.dead_letter.reason": reason})


JOB_METRICS = JobMetrics()


@dataclass(frozen=True, slots=True)
class QueueStats:
    """Point-in-time backlog of the analysis job queue."""

    depth: int
    oldest_ready_age_seconds: float
    dead_letters: int


class _CachedStats:
    """Share one queue read between the gauges of a collection cycle."""

    def __init__(
        self,
        read: Callable[[], QueueStats],
        max_age: float,
        clock: Callable[[], float],
    ) -> None:
        self._read = read
        self._max_age = max_age
        self._clock = clock
        self._lock = threading.Lock()
        self._value: QueueStats | None = None
        self._read_at = 0.0

    def get(self) -> QueueStats | None:
        with self._lock:
            now = self._clock()
            if self._value is None or now - self._read_at >= self._max_age:
                try:
                    self._value = self._read()
                except Exception:  # boundary: a failed read skips one sample
                    self._value = None
                self._read_at = now
            return self._value


def register_queue_gauges(
    read: Callable[[], QueueStats],
    meter: Meter | None = None,
    *,
    max_age: float = 10.0,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Report queue depth, oldest ready message age, and dead letters.

    Values are sampled only while the reporting process runs; see ADR-0014 for
    how idle scale-to-zero affects the alerts built on them.
    """
    active = meter or metrics.get_meter(INSTRUMENTATION_NAME)
    cache = _CachedStats(read, max_age, clock)

    def gauge(field: str) -> Callable[[CallbackOptions], Iterable[Observation]]:
        def observe(_options: CallbackOptions) -> Iterable[Observation]:
            stats = cache.get()
            return [] if stats is None else [Observation(getattr(stats, field))]

        return observe

    active.create_observable_gauge(
        "codebreakers.queue.depth",
        [gauge("depth")],
        unit="{message}",
        description="Messages waiting in the job queue, excluding dead letters.",
    )
    active.create_observable_gauge(
        "codebreakers.queue.oldest_age",
        [gauge("oldest_ready_age_seconds")],
        unit="s",
        description="How long the oldest deliverable message has been waiting.",
    )
    active.create_observable_gauge(
        "codebreakers.queue.dead_letters",
        [gauge("dead_letters")],
        unit="{message}",
        description="Messages in the dead-letter queue.",
    )


_SPAN_KEY = "_codebreakers_span"


def trace_engine(engine: Engine) -> None:
    """Record a client span for each statement executed on ``engine``.

    OpenTelemetry's SQLAlchemy instrumentation does not yet support
    SQLAlchemy 2.1, so these engine events stand in for it. Spans carry the
    parameterized SQL, database name, and server address; bound parameter
    values and the connection credentials are never read.
    """
    url = engine.url
    base: dict[str, str | int] = {"db.system": url.get_backend_name()}
    if url.database:
        base["db.name"] = url.database
    if url.host:
        base["server.address"] = url.host
    if url.port:
        base["server.port"] = url.port

    def start(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        context: Any,
        _executemany: bool,
    ) -> None:
        operation = statement.lstrip().split(None, 1)[0].upper() if statement else "SQL"
        span = trace.get_tracer(INSTRUMENTATION_NAME).start_span(
            operation,
            kind=SpanKind.CLIENT,
            attributes={**base, "db.operation": operation, "db.statement": statement},
        )
        setattr(context, _SPAN_KEY, span)

    def end(
        _conn: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        context: Any,
        _executemany: bool,
    ) -> None:
        span: Span | None = getattr(context, _SPAN_KEY, None)
        if span is not None:
            span.end()

    def fail(error: Any) -> None:
        span: Span | None = getattr(error.execution_context, _SPAN_KEY, None)
        if span is not None:
            span.set_status(
                Status(StatusCode.ERROR, type(error.original_exception).__name__)
            )
            span.end()

    event.listen(engine, "before_cursor_execute", start)
    event.listen(engine, "after_cursor_execute", end)
    event.listen(engine, "handle_error", fail)
