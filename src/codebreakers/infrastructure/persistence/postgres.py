"""SQLAlchemy repository adapter for durable analysis jobs."""

import math
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    create_engine,
    delete,
    exists,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import CursorResult, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisOutcome,
    AnalysisRepository,
    AnalysisStatus,
)
from codebreakers.application.errors import ConcurrentAnalysisUpdateError
from codebreakers.application.messaging import (
    AnalysisJobMessage,
    AnalysisOutbox,
    TraceContext,
)
from codebreakers.domain.cryptanalysis.analyzers import (
    VigenereAnalysisResult,
    VigenereKeyLengthCandidate,
)
from codebreakers.domain.cryptanalysis.models import (
    AnalysisResult,
    Candidate,
    FrequencyAnalysisReport,
)

_NONFINITE_FLOAT_KEY = "__codebreakers_nonfinite_float__"


class Base(DeclarativeBase):
    """Declarative base for persistence models and Alembic metadata."""


class AnalysisRecord(Base):
    """Database representation of a retained analysis job."""

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_analysis_jobs_status",
        ),
        Index("ix_analysis_jobs_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    analyzer: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisInputRecord(Base):
    """Source text and trace context kept until the job reaches a terminal state."""

    __tablename__ = "analysis_job_inputs"

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    traceparent: Mapped[str | None] = mapped_column(String(55))


class OutboxRecord(Base):
    """Job message committed with its job and awaiting publication."""

    __tablename__ = "analysis_outbox"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class JobQueueRecord(Base):
    """Message in the PostgreSQL job queue, a local stand-in for Service Bus.

    Rows are deliberately not linked to ``analysis_jobs``: like broker
    messages, they outlive purged jobs and may be undecodable poison messages.
    """

    __tablename__ = "analysis_job_queue"
    __table_args__ = (
        Index(
            "ix_analysis_job_queue_ready",
            "available_at",
            "id",
            postgresql_where=text("dead_lettered_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delivery_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    lock_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_letter_reason: Mapped[str | None] = mapped_column(String(128))
    dead_letter_description: Mapped[str | None] = mapped_column(Text)


class SqlAlchemyAnalysisRepository(AnalysisRepository, AnalysisOutbox):
    """Persist analysis jobs with optimistic concurrency and bounded retention."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        retention_days: int = 7,
    ) -> None:
        if retention_days < 1:
            msg = "retention_days must be at least 1."
            raise ValueError(msg)
        self._sessions = sessions
        self.retention_days = retention_days

    def add(self, job: AnalysisJob) -> None:
        """Insert a new job without source text or an outbox entry."""
        with self._sessions.begin() as session:
            session.add(_to_record(job))

    def enqueue(
        self, job: AnalysisJob, source_text: str, message: AnalysisJobMessage
    ) -> None:
        """Insert a pending job, its source text, and its outbox message together."""
        with self._sessions.begin() as session:
            session.add(_to_record(job))
            session.flush()
            session.add(
                AnalysisInputRecord(
                    job_id=job.id,
                    source_text=source_text,
                    correlation_id=message.trace.correlation_id,
                    traceparent=message.trace.traceparent,
                )
            )
            session.add(
                OutboxRecord(
                    job_id=job.id,
                    payload=message.to_json(),
                    created_at=job.created_at,
                )
            )

    def recover(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish messages for unfinished jobs that have no outbox entry."""
        if limit < 1:
            msg = "limit must be positive."
            raise ValueError(msg)
        with self._sessions() as session:
            rows = session.execute(
                select(
                    AnalysisRecord.id,
                    AnalysisInputRecord.correlation_id,
                    AnalysisInputRecord.traceparent,
                )
                .outerjoin(
                    AnalysisInputRecord,
                    AnalysisInputRecord.job_id == AnalysisRecord.id,
                )
                .where(
                    AnalysisRecord.status.in_(
                        [AnalysisStatus.PENDING.value, AnalysisStatus.RUNNING.value]
                    ),
                    ~exists().where(OutboxRecord.job_id == AnalysisRecord.id),
                )
                .order_by(AnalysisRecord.created_at)
                .limit(limit)
            ).all()
        for job_id, correlation_id, traceparent in rows:
            trace = TraceContext.sanitized(correlation_id, traceparent)
            publish(AnalysisJobMessage(job_id=job_id, trace=trace))
        return len(rows)

    def get_source_text(self, job_id: UUID) -> str | None:
        """Return retained source text for an unfinished job."""
        with self._sessions() as session:
            record = session.get(AnalysisInputRecord, job_id)
            return None if record is None else record.source_text

    def relay(self, publish: Callable[[AnalysisJobMessage], None], limit: int) -> int:
        """Publish locked outbox rows oldest first, skipping rows held elsewhere.

        ``FOR UPDATE SKIP LOCKED`` lets several relays run concurrently without
        publishing the same row twice. Deletions of rows published before a
        failure are committed before the failure is re-raised.
        """
        if limit < 1:
            msg = "limit must be positive."
            raise ValueError(msg)
        failure: Exception | None = None
        published: list[int] = []
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(OutboxRecord)
                .order_by(OutboxRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                try:
                    publish(AnalysisJobMessage.from_json(row.payload))
                except Exception as err:  # re-raised once earlier deletions commit
                    failure = err
                    break
                published.append(row.id)
            if published:
                session.execute(
                    delete(OutboxRecord).where(OutboxRecord.id.in_(published))
                )
        if failure is not None:
            raise failure
        return len(published)

    def get(self, job_id: UUID) -> AnalysisJob | None:
        """Return one stored job, if present."""
        with self._sessions() as session:
            record = session.get(AnalysisRecord, job_id)
            return None if record is None else _to_job(record)

    def list(self, offset: int, limit: int) -> tuple[AnalysisJob, ...]:
        """Return a newest-first page of jobs."""
        if offset < 0 or limit < 1:
            msg = "offset must be non-negative and limit must be positive."
            raise ValueError(msg)
        with self._sessions() as session:
            records = session.scalars(
                select(AnalysisRecord)
                .order_by(AnalysisRecord.created_at.desc(), AnalysisRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(_to_job(record) for record in records)

    def count(self) -> int:
        """Return the number of retained jobs."""
        with self._sessions() as session:
            return session.scalar(select(func.count()).select_from(AnalysisRecord)) or 0

    def update(self, job: AnalysisJob, expected_version: int) -> AnalysisJob:
        """Update a job only if its version has not changed since it was read."""
        if job.version != expected_version + 1:
            msg = "Updated job version must increment the expected version by one."
            raise ValueError(msg)
        values: dict[str, Any] = {
            "status": job.status.value,
            "updated_at": job.updated_at,
            "completed_at": job.completed_at,
            "result": None if job.result is None else _encode_outcome(job.result),
            "error_code": job.error_code,
            "version": job.version,
            "attempts": job.attempts,
            "lease_expires_at": job.lease_expires_at,
        }
        with self._sessions.begin() as session:
            changed = cast(
                CursorResult[Any],
                session.execute(
                    update(AnalysisRecord)
                    .where(
                        AnalysisRecord.id == job.id,
                        AnalysisRecord.version == expected_version,
                    )
                    .values(**values)
                ),
            )
            if changed.rowcount != 1:
                raise ConcurrentAnalysisUpdateError(
                    f"Analysis '{job.id}' was modified by another operation."
                )
            if job.is_terminal:
                session.execute(
                    delete(AnalysisInputRecord).where(
                        AnalysisInputRecord.job_id == job.id
                    )
                )
        return job

    def delete_expired(self, before: datetime) -> int:
        """Delete jobs created before the cutoff; inputs and outbox rows cascade."""
        with self._sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    delete(AnalysisRecord).where(AnalysisRecord.created_at < before)
                ),
            )
            return result.rowcount or 0

    def purge_expired(self, now: datetime | None = None) -> int:
        """Apply the configured retention window and return the deleted count."""
        current = now or datetime.now(UTC)
        return self.delete_expired(current - timedelta(days=self.retention_days))


def create_postgres_engine(database_url: str, **options: Any) -> Engine:
    """Create a SQLAlchemy engine using psycopg 3 for generic PostgreSQL URLs."""
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, **options)


def _clean_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        kind = (
            "nan"
            if math.isnan(value)
            else "positive_infinity"
            if value > 0
            else "negative_infinity"
        )
        return {_NONFINITE_FLOAT_KEY: kind}
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_clean_json(item) for item in value]
    return value


def _restore_json(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {_NONFINITE_FLOAT_KEY}:
            return {
                "nan": math.nan,
                "positive_infinity": math.inf,
                "negative_infinity": -math.inf,
            }[value[_NONFINITE_FLOAT_KEY]]
        return {key: _restore_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_json(item) for item in value]
    return value


def _encode_outcome(outcome: AnalysisOutcome) -> dict[str, Any]:
    match outcome:
        case AnalysisResult():
            kind = "ranked-candidates"
        case FrequencyAnalysisReport():
            kind = "frequency-report"
        case VigenereAnalysisResult():
            kind = "vigenere-key-analysis"
        case _:
            msg = f"Unsupported analysis result: {type(outcome).__name__}."
            raise TypeError(msg)
    return cast(dict[str, Any], _clean_json({"kind": kind, **asdict(outcome)}))


def _candidate(data: dict[str, Any]) -> Candidate:
    score = data["score"]
    return Candidate(
        rank=data["rank"],
        score=float("nan") if score is None else score,
        key=data["key"],
        text=data["text"],
        explanation=data["explanation"],
    )


def _decode_outcome(data: dict[str, Any]) -> AnalysisOutcome:
    data = _restore_json(data)
    kind = data["kind"]
    if kind == "ranked-candidates":
        return AnalysisResult(
            analyzer=data["analyzer"],
            language=data["language"],
            language_version=data["language_version"],
            candidates=tuple(_candidate(item) for item in data["candidates"]),
        )
    if kind == "frequency-report":
        return FrequencyAnalysisReport(
            analyzer=data["analyzer"],
            language=data["language"],
            language_version=data["language_version"],
            symbol_counts=data["symbol_counts"],
            symbol_frequencies=data["symbol_frequencies"],
            comparisons=data["comparisons"],
            notes=tuple(data["notes"]),
        )
    if kind == "vigenere-key-analysis":
        return VigenereAnalysisResult(
            analyzer=data["analyzer"],
            language=data["language"],
            language_version=data["language_version"],
            key_lengths=tuple(
                VigenereKeyLengthCandidate(**item) for item in data["key_lengths"]
            ),
            candidate_keys=tuple(_candidate(item) for item in data["candidate_keys"]),
            minimum_useful_ciphertext_length=data["minimum_useful_ciphertext_length"],
            limitations=tuple(data["limitations"]),
        )
    msg = f"Unsupported persisted analysis result kind: {kind}."
    raise ValueError(msg)


def _to_record(job: AnalysisJob) -> AnalysisRecord:
    return AnalysisRecord(
        id=job.id,
        analyzer=job.analyzer,
        language=job.language,
        parameters=dict(job.parameters),
        status=job.status.value,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        result=None if job.result is None else _encode_outcome(job.result),
        error_code=job.error_code,
        version=job.version,
        attempts=job.attempts,
        lease_expires_at=job.lease_expires_at,
    )


def _to_job(record: AnalysisRecord) -> AnalysisJob:
    return AnalysisJob(
        id=record.id,
        analyzer=record.analyzer,
        language=record.language,
        status=AnalysisStatus(record.status),
        created_at=record.created_at,
        completed_at=record.completed_at,
        result=None if record.result is None else _decode_outcome(record.result),
        updated_at=record.updated_at,
        error_code=record.error_code,
        version=record.version,
        parameters=record.parameters,
        attempts=record.attempts,
        lease_expires_at=record.lease_expires_at,
    )
