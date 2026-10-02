"""Transport-neutral handling of job messages into settlement decisions."""

import logging

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
from codebreakers.worker.execution import ProcessAnalysisExecutor
from codebreakers.worker.settings import WorkerSettings

logger = logging.getLogger("codebreakers.worker")

INVALID_MESSAGE = "invalid-message"


class JobMessageHandler:
    """Decode a delivery, process it, and choose complete, retry, or dead-letter."""

    def __init__(
        self, processor: AnalysisJobProcessor, retry_policy: RetryPolicy | None = None
    ) -> None:
        self._processor = processor
        self._retry_policy = retry_policy or RetryPolicy()

    def handle(self, body: str | bytes) -> Decision:
        """Return the settlement decision for one raw message body."""
        try:
            message = AnalysisJobMessage.from_json(body)
        except InvalidJobMessageError as err:
            logger.warning("job_message_rejected reason=%s", INVALID_MESSAGE)
            return Decision(Action.DEAD_LETTER, INVALID_MESSAGE, description=str(err))

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
                Action.RETRY, "transient-failure", message.next_attempt(), delay
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
