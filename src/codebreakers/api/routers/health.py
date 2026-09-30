"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter, Response, status

from codebreakers.api.dependencies import ReadinessCheck, ReadinessChecksDep
from codebreakers.api.schemas import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", operation_id="get_liveness", summary="Liveness probe")
def get_liveness() -> LivenessResponse:
    """Report that the process is running."""
    return LivenessResponse()


@router.get(
    "/ready",
    operation_id="get_readiness",
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "Not ready"}},
)
def get_readiness(checks: ReadinessChecksDep, response: Response) -> ReadinessResponse:
    """Report whether dependencies needed to serve traffic are available."""
    results = {name: _run_check(check) for name, check in checks.items()}
    ready = all(result == "ok" for result in results.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not-ready", checks=results)


def _run_check(check: ReadinessCheck) -> Literal["ok", "failing"]:
    try:
        return "ok" if check() else "failing"
    except Exception:  # probe boundary: a crashing check means "not ready"
        return "failing"
