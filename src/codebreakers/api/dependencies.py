"""FastAPI dependency providers."""

from collections.abc import Callable, Mapping
from typing import Annotated, cast

from fastapi import Depends, Request

from codebreakers.application.analysis import AnalysisService
from codebreakers.application.services import CipherOperation
from codebreakers.composition import run_cipher

type CipherRunner = Callable[[str, CipherOperation, str, str, str], str]
type ReadinessCheck = Callable[[], bool]


def get_cipher_runner() -> CipherRunner:
    """Provide the shared cipher runner used by the CLI and API."""
    return run_cipher


def get_analysis_service(request: Request) -> AnalysisService:
    """Provide the analysis service bound to this application instance."""
    return cast(AnalysisService, request.app.state.analysis_service)


def get_readiness_checks(request: Request) -> Mapping[str, ReadinessCheck]:
    """Provide the readiness checks bound to this application instance."""
    return cast(Mapping[str, ReadinessCheck], request.app.state.readiness_checks)


CipherRunnerDep = Annotated[CipherRunner, Depends(get_cipher_runner)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
ReadinessChecksDep = Annotated[
    Mapping[str, ReadinessCheck], Depends(get_readiness_checks)
]
