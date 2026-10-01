"""PostgreSQL repository and Alembic integration tests."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import sessionmaker
from testcontainers.community.postgres import PostgresContainer

from codebreakers.api.app import ApiSettings, create_app
from codebreakers.application.analysis import AnalysisJob, AnalysisStatus
from codebreakers.application.errors import ConcurrentAnalysisUpdateError
from codebreakers.domain.cryptanalysis.models import AnalysisResult, Candidate
from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)

FIXED_TIME = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.mark.unit
def test_generic_postgresql_url_uses_psycopg_driver() -> None:
    engine = create_postgres_engine("postgresql://user:password@localhost/db")
    assert engine.dialect.driver == "psycopg"
    engine.dispose()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Start PostgreSQL and exercise the migration from an empty schema."""
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


def _job(job_id: int, created_at: datetime) -> AnalysisJob:
    return AnalysisJob(
        id=UUID(int=job_id),
        analyzer="caesar-bruteforce",
        language="english",
        status=AnalysisStatus.PENDING,
        created_at=created_at,
        completed_at=None,
        result=None,
        parameters={"language": "english"},
    )


@pytest.mark.integration
def test_migration_creates_expected_schema(database_engine: Engine) -> None:
    inspector = inspect(database_engine)
    assert "analysis_jobs" in inspector.get_table_names()
    assert "source_text" not in {
        column["name"] for column in inspector.get_columns("analysis_jobs")
    }


@pytest.mark.integration
def test_repository_round_trip_pagination_concurrency_and_retention(
    database_engine: Engine,
) -> None:
    repository = SqlAlchemyAnalysisRepository(
        sessionmaker(database_engine), retention_days=7
    )
    result = AnalysisResult(
        analyzer="caesar-bruteforce",
        language="english",
        language_version="test-v1",
        candidates=(
            Candidate(
                rank=1,
                score=float("nan"),
                key="3",
                text="PLAINTEXT",
                explanation="test result",
            ),
        ),
    )
    completed = AnalysisJob(
        id=UUID(int=1),
        analyzer="caesar-bruteforce",
        language="english",
        status=AnalysisStatus.SUCCEEDED,
        created_at=FIXED_TIME,
        completed_at=FIXED_TIME + timedelta(seconds=1),
        updated_at=FIXED_TIME + timedelta(seconds=1),
        result=result,
        version=3,
        parameters={"language": "english"},
    )
    repository.add(completed)
    stored = repository.get(completed.id)
    assert stored is not None
    assert isinstance(stored.result, AnalysisResult)
    assert stored.result.candidates[0].text == "PLAINTEXT"
    assert math.isnan(stored.result.candidates[0].score)

    older = _job(2, FIXED_TIME - timedelta(days=8))
    newest = _job(3, FIXED_TIME + timedelta(seconds=2))
    repository.add(older)
    repository.add(newest)
    page = repository.list(offset=0, limit=2)
    assert tuple(job.id for job in page) == (newest.id, completed.id)
    assert repository.count() == 3

    running = older.transition(AnalysisStatus.RUNNING, FIXED_TIME)
    repository.update(running, expected_version=older.version)
    with pytest.raises(ConcurrentAnalysisUpdateError):
        repository.update(running, expected_version=older.version)

    assert repository.delete_expired(FIXED_TIME - timedelta(days=7)) == 1
    assert repository.get(older.id) is None
    assert repository.count() == 2


@pytest.mark.integration
def test_api_job_survives_application_restart(database_engine: Engine) -> None:
    database_url = database_engine.url.render_as_string(hide_password=False)
    settings = ApiSettings(database_url=database_url)
    with TestClient(create_app(settings)) as first_client:
        created = first_client.post(
            "/v1/analyses",
            json={
                "analyzer": "caesar-bruteforce",
                "text": "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ",
            },
        )
        assert created.status_code == 201
        job = created.json()

    with TestClient(create_app(settings)) as restarted_client:
        fetched = restarted_client.get(f"/v1/analyses/{job['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == job
