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
from codebreakers.api.routers import analyses, ciphers, health
from codebreakers.application.analysis import AnalysisService
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

_DESCRIPTION = (
    "Encrypt, decrypt, and analyze text with classical ciphers. These ciphers "
    "are historically broken and provide no confidentiality; never use them to "
    "protect real secrets."
)


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Runtime limits for the HTTP API."""

    max_body_bytes: int = 65_536
    analysis_capacity: int = 128
    database_url: str | None = field(default=None, repr=False)
    analysis_retention_days: int = field(
        default_factory=lambda: int(
            os.environ.get("CODEBREAKERS_ANALYSIS_RETENTION_DAYS", "7")
        )
    )


def create_app(
    settings: ApiSettings | None = None,
    *,
    analysis_service: AnalysisService | None = None,
    readiness_checks: Mapping[str, ReadinessCheck] | None = None,
) -> FastAPI:
    """Build a configured FastAPI application."""
    active = settings or ApiSettings()
    database_url = active.database_url or os.environ.get("CODEBREAKERS_DATABASE_URL")
    engine: Engine | None = None
    if analysis_service is None and database_url is not None:
        engine = create_postgres_engine(database_url, pool_pre_ping=True)
        repository = SqlAlchemyAnalysisRepository(
            sessionmaker(engine), retention_days=active.analysis_retention_days
        )
        configured_service = create_analysis_service(repository)
    else:
        configured_service = analysis_service or create_analysis_service(
            InMemoryAnalysisRepository(capacity=active.analysis_capacity)
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
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
    app.state.database_engine = engine
    checks: dict[str, ReadinessCheck] = {
        "cipher_registry": lambda: bool(CIPHER_REGISTRY),
        "analyzer_registry": lambda: bool(ANALYZER_REGISTRY),
    }
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
    # Last added runs outermost, so every response gets a correlation ID.
    app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=active.max_body_bytes)
    app.add_middleware(CorrelationIdMiddleware)
    install_openapi(app)
    return app
