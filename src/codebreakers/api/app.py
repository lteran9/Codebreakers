"""FastAPI application factory."""

from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI

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


def create_app(
    settings: ApiSettings | None = None,
    *,
    analysis_service: AnalysisService | None = None,
    readiness_checks: Mapping[str, ReadinessCheck] | None = None,
) -> FastAPI:
    """Build a configured FastAPI application."""
    active = settings or ApiSettings()
    app = FastAPI(
        title="Codebreakers API",
        version=__version__,
        description=_DESCRIPTION,
        responses=problem_responses(500),
        openapi_tags=[
            {"name": "ciphers", "description": "Keyed encryption and decryption."},
            {"name": "analyses", "description": "Keyless cryptanalysis jobs."},
            {"name": "health", "description": "Process and readiness probes."},
        ],
    )
    app.state.analysis_service = analysis_service or create_analysis_service(
        InMemoryAnalysisRepository(capacity=active.analysis_capacity)
    )
    app.state.readiness_checks = readiness_checks or {
        "cipher_registry": lambda: bool(CIPHER_REGISTRY),
        "analyzer_registry": lambda: bool(ANALYZER_REGISTRY),
    }

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
