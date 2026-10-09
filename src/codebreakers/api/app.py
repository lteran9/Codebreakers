"""FastAPI application factory."""

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from codebreakers import __version__
from codebreakers.api.correlation import CorrelationIdMiddleware
from codebreakers.api.dependencies import ReadinessCheck
from codebreakers.api.limits import RequestSizeLimitMiddleware
from codebreakers.api.openapi import install_openapi
from codebreakers.api.problems import problem_responses, register_exception_handlers
from codebreakers.api.rate_limit import RateLimiter, RateLimitMiddleware
from codebreakers.api.routers import analyses, ciphers, health
from codebreakers.application.analysis import AnalysisService
from codebreakers.application.processing import AnalysisExecutor
from codebreakers.composition import (
    ANALYZER_REGISTRY,
    CIPHER_REGISTRY,
    create_analysis_service,
)
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)
from codebreakers.worker.handler import build_handler
from codebreakers.worker.inprocess import InProcessRuntime
from codebreakers.worker.settings import ConfigurationError, WorkerSettings

_DESCRIPTION = (
    "Encrypt, decrypt, and analyze text with classical ciphers. These ciphers "
    "are historically broken and provide no confidentiality; never use them to "
    "protect real secrets."
)


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        msg = f"{name} must be a non-negative integer."
        raise ConfigurationError(msg)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw not in _TRUE | _FALSE:
        msg = f"{name} must be true or false."
        raise ConfigurationError(msg)
    return raw in _TRUE


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Runtime limits for the HTTP API.

    A value of ``0`` disables a rate limit or the unfinished-job cap. Rate
    limits are per client IP and per replica; ``trusted_proxy_hops`` is the
    number of reverse proxies whose ``X-Forwarded-For`` entries are trusted.
    """

    max_body_bytes: int = 65_536
    analysis_capacity: int = 128
    database_url: str | None = field(default=None, repr=False)
    analysis_retention_days: int = field(
        default_factory=lambda: int(
            os.environ.get("CODEBREAKERS_ANALYSIS_RETENTION_DAYS", "7")
        )
    )
    rate_limit_per_minute: int = field(
        default_factory=lambda: _env_int("CODEBREAKERS_RATE_LIMIT_PER_MINUTE", 120)
    )
    analysis_submissions_per_minute: int = field(
        default_factory=lambda: _env_int(
            "CODEBREAKERS_ANALYSIS_SUBMISSIONS_PER_MINUTE", 10
        )
    )
    trusted_proxy_hops: int = field(
        default_factory=lambda: _env_int("CODEBREAKERS_TRUSTED_PROXY_HOPS", 0)
    )
    max_pending_analyses: int = field(
        default_factory=lambda: _env_int("CODEBREAKERS_MAX_PENDING_ANALYSES", 100)
    )
    analysis_listing_enabled: bool = field(
        default_factory=lambda: _env_bool("CODEBREAKERS_ANALYSIS_LISTING_ENABLED", True)
    )
    worker: WorkerSettings = field(default_factory=WorkerSettings.from_env)


def create_app(
    settings: ApiSettings | None = None,
    *,
    analysis_service: AnalysisService | None = None,
    readiness_checks: Mapping[str, ReadinessCheck] | None = None,
    executor: AnalysisExecutor | None = None,
) -> FastAPI:
    """Build a configured FastAPI application.

    With the ``in-process`` queue backend the application also hosts the
    outbox relay and a worker thread, so a single process runs jobs end to end.
    With ``service-bus`` or ``postgres`` it only records jobs; separate
    ``codebreakers relay`` and ``codebreakers worker`` processes publish and
    run them. ``executor`` overrides how the in-process worker runs analyzers,
    mainly for tests. Injected analysis services require a distributed backend.
    """
    active = settings or ApiSettings()
    database_url = active.database_url or os.environ.get("CODEBREAKERS_DATABASE_URL")
    backend = active.worker.queue_backend
    distributed = backend.distributed
    if distributed and database_url is None:
        msg = f"The {backend} queue backend requires CODEBREAKERS_DATABASE_URL."
        raise ConfigurationError(msg)
    if analysis_service is not None and not distributed:
        msg = "An injected analysis_service requires a distributed queue backend."
        raise ConfigurationError(msg)
    engine: Engine | None = None
    runtime: InProcessRuntime | None = None
    if analysis_service is not None:
        configured_service = analysis_service
    else:
        repository: SqlAlchemyAnalysisRepository | InMemoryAnalysisRepository
        if database_url is not None:
            engine = create_postgres_engine(database_url, pool_pre_ping=True)
            repository = SqlAlchemyAnalysisRepository(
                sessionmaker(engine), retention_days=active.analysis_retention_days
            )
        else:
            repository = InMemoryAnalysisRepository(capacity=active.analysis_capacity)
        configured_service = create_analysis_service(
            repository, max_unfinished=active.max_pending_analyses or None
        )
        if not distributed:
            runtime = InProcessRuntime(
                repository, build_handler(repository, active.worker, executor)
            )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            runtime.start()
        try:
            yield
        finally:
            if runtime is not None:
                runtime.stop()
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        title="Codebreakers API",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
        responses=problem_responses(500),
        openapi_tags=[
            {"name": "ciphers", "description": "Keyed encryption and decryption."},
            {"name": "analyses", "description": "Keyless cryptanalysis jobs."},
            {"name": "health", "description": "Process and readiness probes."},
        ],
    )
    app.state.analysis_service = configured_service
    app.state.analysis_listing_enabled = active.analysis_listing_enabled
    app.state.database_engine = engine
    app.state.job_runtime = runtime
    checks: dict[str, ReadinessCheck] = {
        "cipher_registry": lambda: bool(CIPHER_REGISTRY),
        "analyzer_registry": lambda: bool(ANALYZER_REGISTRY),
    }
    if runtime is not None:
        checks["analysis_worker"] = runtime.is_running
    if readiness_checks is not None:
        checks = dict(readiness_checks)
    elif engine is not None:

        def check_database() -> bool:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            except SQLAlchemyError:
                return False
            return True

        checks["database"] = check_database
    app.state.readiness_checks = checks

    v1 = APIRouter(prefix="/v1")
    v1.include_router(ciphers.router)
    v1.include_router(analyses.router)
    app.include_router(v1)
    app.include_router(health.router)

    register_exception_handlers(app)
    # Last added runs outermost, so every response gets a correlation ID and
    # rate-limited requests are rejected before their bodies are read.
    app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=active.max_body_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        general=_limiter(active.rate_limit_per_minute),
        submissions=_limiter(active.analysis_submissions_per_minute),
        submission_path=app.url_path_for("create_analysis"),
        trusted_proxy_hops=active.trusted_proxy_hops,
    )
    app.add_middleware(CorrelationIdMiddleware)
    install_openapi(app)
    return app


def _limiter(per_minute: int) -> RateLimiter | None:
    return RateLimiter(per_minute) if per_minute > 0 else None
