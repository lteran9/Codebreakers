"""Composition root for the outbox relay and analysis worker processes."""

import logging
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta

from azure.servicebus import ServiceBusClient, ServiceBusSender
from sqlalchemy.orm import Session, sessionmaker

from codebreakers.application.messaging import JobPublisher
from codebreakers.infrastructure.messaging.postgres_queue import (
    PostgresDeadLetterQueue,
    PostgresJobPublisher,
    PostgresQueueReceiver,
    PostgresQueueStats,
)
from codebreakers.infrastructure.messaging.servicebus import (
    ServiceBusDeadLetterQueue,
    ServiceBusJobConsumer,
    ServiceBusJobPublisher,
)
from codebreakers.infrastructure.messaging.settlement import (
    DeadLetterQueue,
    JobConsumer,
)
from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)
from codebreakers.infrastructure.telemetry import (
    TracingJobPublisher,
    register_queue_gauges,
)
from codebreakers.worker.handler import build_handler
from codebreakers.worker.heartbeat import Heartbeat
from codebreakers.worker.settings import QueueBackend, WorkerSettings

logger = logging.getLogger("codebreakers.worker")

# Message locks outlast the time budget to cover start-up and saving.
_LOCK_RENEWAL_MARGIN = timedelta(minutes=1)
# Short waits keep the PostgreSQL consumer responsive to shutdown signals.
_POSTGRES_RECEIVE_WAIT = 1.0
_POSTGRES_QUEUE = "analysis_job_queue"


@contextmanager
def _postgres_sessions(settings: WorkerSettings) -> Iterator[sessionmaker[Session]]:
    engine = create_postgres_engine(settings.require_database_url(), pool_pre_ping=True)
    try:
        yield sessionmaker(engine)
    finally:
        engine.dispose()


def _repository(
    sessions: sessionmaker[Session], settings: WorkerSettings
) -> SqlAlchemyAnalysisRepository:
    return SqlAlchemyAnalysisRepository(
        sessions, retention_days=settings.retention_days
    )


@contextmanager
def _postgres_repository(
    settings: WorkerSettings,
) -> Iterator[SqlAlchemyAnalysisRepository]:
    with _postgres_sessions(settings) as sessions:
        yield _repository(sessions, settings)


def _service_bus_client(settings: WorkerSettings) -> ServiceBusClient:
    return ServiceBusClient.from_connection_string(settings.require_servicebus())


def _heartbeat(settings: WorkerSettings) -> Callable[[], None] | None:
    if settings.heartbeat_file is None:
        return None
    return Heartbeat(settings.heartbeat_file).beat


def _uses_postgres_queue(settings: WorkerSettings) -> bool:
    return settings.queue_backend is QueueBackend.POSTGRES


def _postgres_publisher(sessions: sessionmaker[Session]) -> JobPublisher:
    return TracingJobPublisher(
        PostgresJobPublisher(sessions), "postgresql", _POSTGRES_QUEUE
    )


def _service_bus_publisher(sender: ServiceBusSender, queue: str) -> JobPublisher:
    return TracingJobPublisher(ServiceBusJobPublisher(sender), "servicebus", queue)


def run_worker(settings: WorkerSettings, stop: threading.Event) -> None:
    """Consume job messages until ``stop`` is set.

    The ``postgres`` backend reads the PostgreSQL job queue; any other backend
    reads Azure Service Bus.
    """
    lock_duration = settings.time_budget + _LOCK_RENEWAL_MARGIN
    heartbeat = _heartbeat(settings)
    if _uses_postgres_queue(settings):
        with _postgres_sessions(settings) as sessions:
            handler = build_handler(_repository(sessions, settings), settings)
            consumer = JobConsumer(handler.handle, _postgres_publisher(sessions))
            register_queue_gauges(PostgresQueueStats(sessions).read)
            logger.info("worker_started queue=postgres")
            consumer.consume(
                PostgresQueueReceiver(sessions, lock_duration=lock_duration),
                stop,
                max_wait_time=_POSTGRES_RECEIVE_WAIT,
                heartbeat=heartbeat,
            )
    else:
        with (
            _postgres_repository(settings) as repository,
            _service_bus_client(settings) as client,
            client.get_queue_sender(settings.queue_name) as sender,
        ):
            handler = build_handler(repository, settings)
            sb_consumer = ServiceBusJobConsumer(
                handler.handle, _service_bus_publisher(sender, settings.queue_name)
            )
            logger.info("worker_started queue=%s", settings.queue_name)
            sb_consumer.run(
                client,
                settings.queue_name,
                stop,
                lock_renewal=lock_duration,
                heartbeat=heartbeat,
            )
    logger.info("worker_stopped")


@contextmanager
def _relay_endpoints(
    settings: WorkerSettings,
) -> Iterator[tuple[SqlAlchemyAnalysisRepository, JobPublisher]]:
    if _uses_postgres_queue(settings):
        with _postgres_sessions(settings) as sessions:
            yield _repository(sessions, settings), _postgres_publisher(sessions)
        return
    with (
        _postgres_repository(settings) as repository,
        _service_bus_client(settings) as client,
        client.get_queue_sender(settings.queue_name) as sender,
    ):
        yield repository, _service_bus_publisher(sender, settings.queue_name)


def run_relay(settings: WorkerSettings, stop: threading.Event) -> None:
    """Publish committed outbox entries to the job queue until ``stop`` is set."""
    heartbeat = _heartbeat(settings)
    with _relay_endpoints(settings) as (repository, publisher):
        logger.info("relay_started backend=%s", settings.queue_backend)
        while not stop.is_set():
            if heartbeat is not None:
                heartbeat()
            try:
                relayed = repository.relay(publisher.publish, settings.relay_batch_size)
            except Exception as err:  # process boundary: back off and keep relaying
                logger.error("outbox_relay_error error=%s", type(err).__name__)
                relayed = 0
            if relayed:
                logger.info("outbox_relayed count=%d", relayed)
            else:
                stop.wait(settings.relay_poll_interval)
    logger.info("relay_stopped")


@contextmanager
def dead_letter_queue(settings: WorkerSettings) -> Iterator[DeadLetterQueue]:
    """Yield dead-letter operations for the configured queue backend."""
    if _uses_postgres_queue(settings):
        with _postgres_sessions(settings) as sessions:
            yield PostgresDeadLetterQueue(sessions)
        return
    with _service_bus_client(settings) as client:
        yield ServiceBusDeadLetterQueue(client, settings.queue_name)


def install_stop_signals(stop: threading.Event) -> None:
    """Stop gracefully on SIGINT or SIGTERM after the current message finishes."""

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("shutdown_requested signal=%s", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
