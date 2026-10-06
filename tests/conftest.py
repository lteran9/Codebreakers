"""Shared test fixtures: telemetry isolation and the PostgreSQL integration suites."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine


@pytest.fixture(autouse=True)
def _no_telemetry_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's Application Insights settings out of test runs."""
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)


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
