"""PostgreSQL repository and Alembic integration tests."""

import math
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from codebreakers.api.app import ApiSettings, create_app
from codebreakers.application.analysis import AnalysisJob, AnalysisStatus
from codebreakers.application.errors import ConcurrentAnalysisUpdateError
from codebreakers.application.messaging import AnalysisJobMessage, TraceContext
from codebreakers.domain.cryptanalysis.models import AnalysisResult, Candidate
from codebreakers.infrastructure.persistence.postgres import (
    OutboxRecord,
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)
from codebreakers.worker.execution import InlineAnalysisExecutor
from codebreakers.worker.settings import WorkerSettings

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


@pytest.fixture
def repository(database_engine: Engine) -> SqlAlchemyAnalysisRepository:
    """Return a repository over freshly emptied tables."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE analysis_jobs CASCADE"))
    return SqlAlchemyAnalysisRepository(sessionmaker(database_engine), retention_days=7)


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
    assert {"analysis_jobs", "analysis_job_inputs", "analysis_outbox"} <= set(
        inspector.get_table_names()
    )
    job_columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
    assert {"attempts", "lease_expires_at"} <= job_columns
    assert "source_text" not in job_columns
    for table in ("analysis_job_inputs", "analysis_outbox"):
        (foreign_key,) = inspector.get_foreign_keys(table)
        assert foreign_key["referred_table"] == "analysis_jobs"
        assert foreign_key["options"]["ondelete"] == "CASCADE"


@pytest.mark.integration
def test_migration_upgrades_jobs_from_the_previous_schema(
    database_engine: Engine,
) -> None:
    with database_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as admin:
        admin.execute(text("DROP DATABASE IF EXISTS upgrade_check"))
        admin.execute(text("CREATE DATABASE upgrade_check"))
    url = database_engine.url.set(database="upgrade_check")
    engine = create_engine(url)
    config = Config("alembic.ini")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_analysis_jobs")
            connection.execute(
                text(
                    "INSERT INTO analysis_jobs (id, analyzer, language, parameters, "
                    "status, created_at, version) VALUES (:id, 'caesar-bruteforce', "
                    "'english', '{}', 'running', :at, 2)"
                ),
                {"id": UUID(int=42), "at": FIXED_TIME},
            )
            command.upgrade(config, "head")
            row = connection.execute(
                text("SELECT attempts, lease_expires_at FROM analysis_jobs")
            ).one()
            assert tuple(row) == (0, None)
            command.downgrade(config, "0001_analysis_jobs")
            assert (
                connection.execute(
                    text("SELECT count(*) FROM analysis_jobs")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_repository_round_trip_pagination_concurrency_and_retention(
    repository: SqlAlchemyAnalysisRepository,
) -> None:
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


def _message(job_id: UUID) -> AnalysisJobMessage:
    return AnalysisJobMessage(job_id=job_id, trace=TraceContext("req-1"))


@pytest.mark.integration
def test_enqueue_keeps_input_until_terminal_and_relays_once(
    repository: SqlAlchemyAnalysisRepository,
) -> None:
    pending = _job(10, FIXED_TIME)
    repository.enqueue(pending, "KHOOR", _message(pending.id))
    assert repository.get_source_text(pending.id) == "KHOOR"

    published: list[AnalysisJobMessage] = []
    assert repository.relay(published.append, limit=10) == 1
    assert repository.relay(published.append, limit=10) == 0
    assert published == [_message(pending.id)]

    lease = timedelta(seconds=45)
    claimed = pending.claim(FIXED_TIME, lease)
    repository.update(claimed, expected_version=pending.version)
    stored = repository.get(pending.id)
    assert stored is not None
    assert (stored.attempts, stored.lease_expires_at) == (1, FIXED_TIME + lease)
    assert repository.get_source_text(pending.id) == "KHOOR"

    released = claimed.release(FIXED_TIME)
    repository.update(released, expected_version=claimed.version)
    assert repository.get_source_text(pending.id) == "KHOOR"

    failed = released.transition(
        AnalysisStatus.FAILED, FIXED_TIME, error_code="insufficient-text"
    )
    repository.update(failed, expected_version=released.version)
    assert repository.get_source_text(pending.id) is None


@pytest.mark.integration
def test_relay_skips_rows_locked_by_a_concurrent_relay(
    database_engine: Engine, repository: SqlAlchemyAnalysisRepository
) -> None:
    first, second = _job(21, FIXED_TIME), _job(22, FIXED_TIME)
    repository.enqueue(first, "A", _message(first.id))
    repository.enqueue(second, "B", _message(second.id))

    published: list[UUID] = []
    with Session(database_engine) as other_relay, other_relay.begin():
        locked = other_relay.scalars(
            select(OutboxRecord)
            .order_by(OutboxRecord.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).one()
        assert locked.job_id == first.id
        assert repository.relay(lambda m: published.append(m.job_id), limit=10) == 1

    assert repository.relay(lambda m: published.append(m.job_id), limit=10) == 1
    assert published == [second.id, first.id]


@pytest.mark.integration
def test_relay_commits_progress_made_before_a_publish_failure(
    repository: SqlAlchemyAnalysisRepository,
) -> None:
    jobs = [_job(30 + n, FIXED_TIME) for n in range(3)]
    for job in jobs:
        repository.enqueue(job, "TEXT", _message(job.id))
    published: list[UUID] = []

    def fail_second(message: AnalysisJobMessage) -> None:
        if published:
            raise ConnectionError("broker down")
        published.append(message.job_id)

    with pytest.raises(ConnectionError):
        repository.relay(fail_second, limit=10)

    remaining: list[UUID] = []
    assert repository.relay(lambda m: remaining.append(m.job_id), limit=10) == 2
    assert published + remaining == [job.id for job in jobs]


@pytest.mark.integration
def test_retention_purge_cascades_to_inputs_and_outbox(
    database_engine: Engine, repository: SqlAlchemyAnalysisRepository
) -> None:
    expired = _job(40, FIXED_TIME - timedelta(days=8))
    repository.enqueue(expired, "OLD", _message(expired.id))

    assert repository.purge_expired(now=FIXED_TIME) == 1

    with database_engine.connect() as connection:
        for table in ("analysis_job_inputs", "analysis_outbox"):
            count = connection.execute(text(f"SELECT count(*) FROM {table}"))
            assert count.scalar_one() == 0


def _api_settings(database_engine: Engine) -> ApiSettings:
    return ApiSettings(
        database_url=database_engine.url.render_as_string(hide_password=False),
        worker=WorkerSettings(),
    )


def _wait_for_terminal(client: TestClient, location: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while True:
        job: dict[str, Any] = client.get(location).json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        assert time.monotonic() < deadline, job
        time.sleep(0.05)


@pytest.mark.integration
def test_api_job_survives_application_restart(
    database_engine: Engine, repository: SqlAlchemyAnalysisRepository
) -> None:
    settings = _api_settings(database_engine)
    executor = InlineAnalysisExecutor()
    with TestClient(create_app(settings, executor=executor)) as first_client:
        created = first_client.post(
            "/v1/analyses",
            json={
                "analyzer": "caesar-bruteforce",
                "text": "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ",
            },
        )
        assert created.status_code == 202
        job = _wait_for_terminal(first_client, created.headers["location"])
        assert job["status"] == "succeeded"

    with TestClient(create_app(settings, executor=executor)) as restarted_client:
        fetched = restarted_client.get(f"/v1/analyses/{job['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == job


@pytest.mark.integration
def test_jobs_accepted_during_a_worker_outage_complete_after_recovery(
    database_engine: Engine, repository: SqlAlchemyAnalysisRepository
) -> None:
    settings = _api_settings(database_engine)
    executor = InlineAnalysisExecutor()
    # Without the lifespan, neither the relay nor the worker is running.
    outage_client = TestClient(create_app(settings, executor=executor))
    created = outage_client.post(
        "/v1/analyses",
        json={"analyzer": "caesar-bruteforce", "text": "KHOOR ZRUOG"},
    )
    assert created.status_code == 202
    location = created.headers["location"]
    assert outage_client.get(location).json()["status"] == "pending"

    with TestClient(create_app(settings, executor=executor)) as recovered_client:
        job = _wait_for_terminal(recovered_client, location)

    assert job["status"] == "succeeded"


@pytest.mark.integration
def test_recover_republishes_unfinished_jobs_without_outbox_entries(
    repository: SqlAlchemyAnalysisRepository,
) -> None:
    relayed, done, unrelayed = (_job(50 + n, FIXED_TIME) for n in range(3))
    for job in (relayed, done, unrelayed):
        repository.enqueue(job, "TEXT", _message(job.id))
    assert repository.relay(lambda _m: None, limit=2) == 2
    finished = done.transition(AnalysisStatus.FAILED, FIXED_TIME, error_code="x")
    repository.update(finished, expected_version=done.version)

    recovered: list[UUID] = []
    assert repository.recover(lambda m: recovered.append(m.job_id), limit=10) == 1
    assert recovered == [relayed.id]


@pytest.mark.integration
def test_in_process_restart_recovers_jobs_lost_from_the_memory_queue(
    database_engine: Engine, repository: SqlAlchemyAnalysisRepository
) -> None:
    settings = _api_settings(database_engine)
    executor = InlineAnalysisExecutor()
    crashed_app = create_app(settings, executor=executor)
    created = TestClient(crashed_app).post(
        "/v1/analyses",
        json={"analyzer": "caesar-bruteforce", "text": "KHOOR ZRUOG"},
    )
    # Relayed into the in-memory queue, then the process "crashes".
    assert crashed_app.state.job_runtime.relay_once() == 1

    with TestClient(create_app(settings, executor=executor)) as restarted:
        job = _wait_for_terminal(restarted, created.headers["location"])

    assert job["status"] == "succeeded"
