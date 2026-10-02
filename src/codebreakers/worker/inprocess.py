"""In-process job queue and runtime for tests and single-process local use."""

import heapq
import itertools
import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from codebreakers.application.messaging import AnalysisJobMessage, AnalysisOutbox
from codebreakers.application.processing import Action
from codebreakers.worker.handler import JobMessageHandler

logger = logging.getLogger("codebreakers.worker")


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """A message the in-process queue could not deliver successfully."""

    body: str
    reason: str
    description: str | None


class InProcessJobQueue:
    """Thread-safe delayed queue implementing the ``JobPublisher`` port.

    Messages are stored serialized, exactly as a broker would carry them, so
    tests exercise the same schema as Service Bus.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, str]] = []
        self._sequence = itertools.count()
        self._condition = threading.Condition()
        self._dead_letters: list[DeadLetter] = []

    def publish(
        self, message: AnalysisJobMessage, delay: timedelta = timedelta(0)
    ) -> None:
        """Enqueue a message that becomes receivable after ``delay``."""
        due = time.monotonic() + max(delay.total_seconds(), 0.0)
        with self._condition:
            heapq.heappush(self._heap, (due, next(self._sequence), message.to_json()))
            self._condition.notify_all()

    def receive(self, timeout: float = 0.0) -> str | None:
        """Return the next due message body, waiting up to ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                now = time.monotonic()
                if self._heap and self._heap[0][0] <= now:
                    return heapq.heappop(self._heap)[2]
                remaining = deadline - now
                if remaining <= 0:
                    return None
                if self._heap:
                    remaining = min(remaining, self._heap[0][0] - now)
                self._condition.wait(remaining)

    def dead_letter(self, body: str, reason: str, description: str | None) -> None:
        """Park a message that must not be retried automatically."""
        with self._condition:
            self._dead_letters.append(DeadLetter(body, reason, description))

    @property
    def dead_letters(self) -> tuple[DeadLetter, ...]:
        """Return the dead-lettered messages in arrival order."""
        with self._condition:
            return tuple(self._dead_letters)

    def __len__(self) -> int:
        with self._condition:
            return len(self._heap)


class InProcessRuntime:
    """Relay the outbox into an in-process queue and process it on threads.

    The queue lives in memory, so on start the runtime re-publishes unfinished
    jobs whose outbox entries were already relayed before a restart.
    """

    def __init__(
        self,
        outbox: AnalysisOutbox,
        handler: JobMessageHandler,
        queue: InProcessJobQueue | None = None,
        poll_interval: float = 0.1,
        relay_batch_size: int = 100,
        recovery_limit: int = 10_000,
    ) -> None:
        self.queue = queue or InProcessJobQueue()
        self._outbox = outbox
        self._handler = handler
        self._poll_interval = poll_interval
        self._relay_batch_size = relay_batch_size
        self._recovery_limit = recovery_limit
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start the relay and worker threads."""
        self._stop.clear()
        self._threads = [
            threading.Thread(
                target=self._relay_loop, name="analysis-relay", daemon=True
            ),
            threading.Thread(
                target=self._worker_loop, name="analysis-worker", daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the threads to stop and wait for the current message to finish."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout)
        self._threads = []

    def is_running(self) -> bool:
        """Return whether both background threads are alive."""
        return bool(self._threads) and all(t.is_alive() for t in self._threads)

    def recover_once(self) -> int:
        """Re-queue unfinished jobs that a previous process had already relayed."""
        recovered = self._outbox.recover(self.queue.publish, self._recovery_limit)
        if recovered:
            logger.info("jobs_recovered count=%d", recovered)
        return recovered

    def relay_once(self) -> int:
        """Move one batch of outbox entries into the queue."""
        return self._outbox.relay(self.queue.publish, self._relay_batch_size)

    def process_once(self, timeout: float = 0.0) -> bool:
        """Handle at most one due message and return whether one was handled."""
        body = self.queue.receive(timeout)
        if body is None:
            return False
        decision = self._handler.handle(body)
        if decision.action is Action.RETRY:
            assert decision.message is not None
            self.queue.publish(decision.message, decision.delay)
        elif decision.action is Action.DEAD_LETTER:
            self.queue.dead_letter(body, decision.reason, decision.description)
        return True

    def run_until_idle(self) -> None:
        """Synchronously relay and process until nothing is due (for tests)."""
        while self.relay_once() or self.process_once():
            pass

    def _relay_loop(self) -> None:
        recovered = False
        while not self._stop.is_set():
            try:
                if not recovered:
                    self.recover_once()
                    recovered = True
                relayed = self.relay_once()
            except Exception as err:  # thread boundary: keep relaying
                stage = "outbox_relay_error" if recovered else "job_recovery_error"
                logger.error("%s error=%s", stage, type(err).__name__)
                relayed = 0
            if not relayed:
                self._stop.wait(self._poll_interval)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once(self._poll_interval)
            except Exception as err:  # thread boundary: keep consuming
                logger.error("job_worker_error error=%s", type(err).__name__)
