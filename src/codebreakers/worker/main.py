"""Composition root for the outbox relay and analysis worker processes."""

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from azure.servicebus import ServiceBusClient
from sqlalchemy.orm import sessionmaker

from codebreakers.infrastructure.messaging.servicebus import (
    ServiceBusDeadLetterQueue,
    ServiceBusJobConsumer,
    ServiceBusJobPublisher,
)
from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)
from codebreakers.worker.handler import build_handler
from codebreakers.worker.settings import WorkerSettings

logger = logging.getLogger("codebreakers.worker")

# Service Bus locks are renewed past the time budget to cover start-up and saving.
_LOCK_RENEWAL_MARGIN = timedelta(minutes=1)


@contextmanager
def _postgres_repository(
    settings: WorkerSettings,
) -> Iterator[SqlAlchemyAnalysisRepository]:
    engine = create_postgres_engine(settings.require_database_url(), pool_pre_ping=True)
    try:
        yield SqlAlchemyAnalysisRepository(
            sessionmaker(engine), retention_days=settings.retention_days
        )
    finally:
        engine.dispose()


def _service_bus_client(settings: WorkerSettings) -> ServiceBusClient:
    return ServiceBusClient.from_connection_string(settings.require_servicebus())


def run_worker(settings: WorkerSettings, stop: threading.Event) -> None:
    """Consume Service Bus job messages until ``stop`` is set."""
    with (
        _postgres_repository(settings) as repository,
        _service_bus_client(settings) as client,
        client.get_queue_sender(settings.queue_name) as sender,
    ):
        handler = build_handler(repository, settings)
        consumer = ServiceBusJobConsumer(handler.handle, ServiceBusJobPublisher(sender))
        logger.info("worker_started queue=%s", settings.queue_name)
        consumer.run(
            client,
            settings.queue_name,
            stop,
            lock_renewal=settings.time_budget + _LOCK_RENEWAL_MARGIN,
        )
    logger.info("worker_stopped")


def run_relay(settings: WorkerSettings, stop: threading.Event) -> None:
    """Publish committed outbox entries to Service Bus until ``stop`` is set."""
    with (
        _postgres_repository(settings) as repository,
        _service_bus_client(settings) as client,
        client.get_queue_sender(settings.queue_name) as sender,
    ):
        publisher = ServiceBusJobPublisher(sender)
        logger.info("relay_started queue=%s", settings.queue_name)
        while not stop.is_set():
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
def dead_letter_queue(settings: WorkerSettings) -> Iterator[ServiceBusDeadLetterQueue]:
    """Yield dead-letter operations over a client that is closed afterwards."""
    with _service_bus_client(settings) as client:
        yield ServiceBusDeadLetterQueue(client, settings.queue_name)


def install_stop_signals(stop: threading.Event) -> None:
    """Stop gracefully on SIGINT or SIGTERM after the current message finishes."""

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("shutdown_requested signal=%s", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
