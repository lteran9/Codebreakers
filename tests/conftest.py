"""Shared test fixtures: telemetry isolation and the PostgreSQL integration suites."""

import logging
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.util._once import Once
from sqlalchemy import Engine, create_engine


@pytest.fixture(autouse=True)
def _no_telemetry_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's Application Insights settings out of test runs."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Undo process-wide logging set up by commands such as ``serve``."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(factory)
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Install an in-memory tracer provider as the global one for one test.

    OpenTelemetry allows setting the global provider only once per process,
    so the private guard is reset and restored to keep tests independent.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    saved = (trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE)
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE = saved


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    """Start PostgreSQL and exercise the migrations from an empty schema."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        engine = create_engine(postgres.get_connection_url(), pool_pre_ping=True)
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.downgrade(config, "base")
            command.upgrade(config, "head")
        yield engine
        engine.dispose()
