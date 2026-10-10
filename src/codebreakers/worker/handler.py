"""Transport-neutral handling of job messages into settlement decisions."""

import logging
import time

from opentelemetry.trace import SpanKind

from codebreakers.application.analysis import AnalysisRepository
from codebreakers.application.errors import InvalidJobMessageError
from codebreakers.application.messaging import AnalysisJobMessage
from codebreakers.application.processing import (
    RETRIES_EXHAUSTED,
    Action,
    AnalysisExecutor,
    AnalysisJobProcessor,
    Decision,
    ProcessingStatus,
    RetryPolicy,
)
from codebreakers.infrastructure.structured_logging import bind_log_context
from codebreakers.infrastructure.telemetry import (
    JOB_METRICS,
    OUTCOME_ATTRIBUTE,
    JobMetrics,
    job_span,
)
from codebreakers.worker.execution import ProcessAnalysisExecutor
from codebreakers.worker.settings import WorkerSettings

logger = logging.getLogger("codebreakers.worker")

INVALID_MESSAGE = "invalid-message"
TRANSIENT_FAILURE = "transient-failure"


class JobMessageHandler:
    """Decode a delivery, process it, and choose complete, retry, or dead-letter.

    Each delivery runs in a consumer span parented on the message's trace
    context, with its job and correlation IDs bound to every log record.
    """

    def __init__(
        self,
        processor: AnalysisJobProcessor,
        retry_policy: RetryPolicy | None = None,
        metrics: JobMetrics = JOB_METRICS,
    ) -> None:
        self._processor = processor
        self._retry_policy = retry_policy or RetryPolicy()
        self._metrics = metrics

    def handle(self, body: str | bytes) -> Decision:
        """Return the settlement decision for one raw message body."""
        try:
            message = AnalysisJobMessage.from_json(body)
        except InvalidJobMessageError as err:
            logger.warning("job_message_rejected reason=%s", INVALID_MESSAGE)
            self._metrics.dead_lettered(INVALID_MESSAGE)
            return Decision(Action.DEAD_LETTER, INVALID_MESSAGE, description=str(err))

        with (
            bind_log_context(
                job_id=str(message.job_id),
                correlation_id=message.trace.correlation_id,
            ),
            job_span(
                "process analysis-job",
                SpanKind.CONSUMER,
                message,
                {"messaging.operation.type": "process"},
            ) as span,
        ):
            started = time.perf_counter()
            decision = self._decide(message)
            span.set_attribute(OUTCOME_ATTRIBUTE, decision.reason)
            self._metrics.processed(decision.reason, time.perf_counter() - started)
            if decision.action is Action.DEAD_LETTER:
                self._metrics.dead_lettered(decision.reason)
            elif decision.reason == TRANSIENT_FAILURE:
                self._metrics.retried()
            return decision

    def _decide(self, message: AnalysisJobMessage) -> Decision:
        context = _log_context(message)
        try:
            result = self._processor.process(message)
        except Exception as err:  # boundary: infrastructure failures are retried
            return self._retry_or_dead_letter(message, err, context)

        if result.status is ProcessingStatus.DEFERRED:
            logger.info("job_deferred reason=%s %s", result.reason, context)
            return Decision(Action.RETRY, result.reason, message, result.delay)
        logger.info("job_processed outcome=%s %s", result.reason, context)
        return Decision(Action.COMPLETE, result.reason)

    def _retry_or_dead_letter(
        self, message: AnalysisJobMessage, err: Exception, context: str
    ) -> Decision:
        error_type = type(err).__name__
        if self._retry_policy.can_retry(message.attempt):
            delay = self._retry_policy.delay_after(message.attempt)
            logger.warning(
                "job_retry_scheduled error=%s delay_s=%.1f %s",
                error_type,
                delay.total_seconds(),
                context,
            )
            return Decision(
                Action.RETRY, TRANSIENT_FAILURE, message.next_attempt(), delay
            )
        try:
            self._processor.fail_exhausted(message)
        except Exception as mark_err:  # boundary: dead-lettering must still happen
            logger.error(
                "job_fail_exhausted_error error=%s %s",
                type(mark_err).__name__,
                context,
            )
        logger.error("job_dead_lettered error=%s %s", error_type, context)
        return Decision(Action.DEAD_LETTER, RETRIES_EXHAUSTED, description=error_type)


def _log_context(message: AnalysisJobMessage) -> str:
    return (
        f"job_id={message.job_id} attempt={message.attempt} "
        f"correlation_id={message.trace.correlation_id or '-'} "
        f"traceparent={message.trace.traceparent or '-'}"
    )


def build_handler(
    repository: AnalysisRepository,
    settings: WorkerSettings,
    executor: AnalysisExecutor | None = None,
) -> JobMessageHandler:
    """Wire the processor, budget, and retry policy into a message handler."""
    processor = AnalysisJobProcessor(
        repository, executor or ProcessAnalysisExecutor(), settings.budget()
    )
    return JobMessageHandler(processor, settings.retry_policy())
