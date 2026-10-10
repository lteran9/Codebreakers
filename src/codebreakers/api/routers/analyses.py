"""Analysis job routes."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from codebreakers.api.correlation import correlation_id_from_scope
from codebreakers.api.dependencies import AnalysisServiceDep
from codebreakers.api.problems import problem_responses
from codebreakers.api.schemas import (
    AnalysisJobPageResponse,
    AnalysisJobResponse,
    AnalysisRequest,
)
from codebreakers.application.messaging import TraceContext
from codebreakers.infrastructure.structured_logging import bind_log_context
from codebreakers.infrastructure.telemetry import current_traceparent

TRACEPARENT_HEADER = "traceparent"

logger = logging.getLogger("codebreakers.api")

router = APIRouter(prefix="/analyses", tags=["analyses"])


@router.get(
    "",
    operation_id="list_analyses",
    summary="List analyses",
    responses=problem_responses(404, 422, 429),
)
def list_analyses(
    request: Request,
    service: AnalysisServiceDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> AnalysisJobPageResponse:
    """Return retained jobs newest first using offset and limit pagination.

    Jobs are not tied to a user, so a shared deployment disables listing and
    answers `404`; each job is then reachable only through its own URL.
    """
    if not request.app.state.analysis_listing_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    jobs = service.list(offset, limit)
    return AnalysisJobPageResponse(
        items=[AnalysisJobResponse.from_job(job) for job in jobs],
        total=service.count(),
        offset=offset,
        limit=limit,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_analysis",
    summary="Submit an analysis",
    responses=problem_responses(413, 422, 429, 503),
)
def create_analysis(
    body: AnalysisRequest,
    request: Request,
    response: Response,
    service: AnalysisServiceDep,
) -> AnalysisJobResponse:
    """Queue a keyless analysis and return the pending job resource.

    A worker runs the analysis asynchronously. Poll the `Location` URL until
    `status` is `succeeded`, `failed`, or `cancelled`. Clients over their
    submission rate get `429`, and a full job backlog answers `503`; both
    include `Retry-After`. The request's trace
    context (or a valid inbound W3C `traceparent` header when tracing is off)
    is propagated to the worker with the correlation ID.
    """
    trace = TraceContext.sanitized(
        correlation_id_from_scope(request.scope),
        current_traceparent() or request.headers.get(TRACEPARENT_HEADER),
    )
    job = service.submit(body.analyzer, body.text, body.language, trace)
    with bind_log_context(job_id=str(job.id), correlation_id=trace.correlation_id):
        logger.info("analysis_submitted analyzer=%s", job.analyzer)
    response.headers["Location"] = request.app.url_path_for(
        "get_analysis", analysis_id=str(job.id)
    )
    return AnalysisJobResponse.from_job(job)


@router.get(
    "/{analysis_id}",
    operation_id="get_analysis",
    summary="Get an analysis",
    responses=problem_responses(404, 422, 429),
)
def get_analysis(analysis_id: UUID, service: AnalysisServiceDep) -> AnalysisJobResponse:
    """Return a previously submitted analysis job and its current status.

    Jobs are durable when PostgreSQL is configured; otherwise they are held in
    process memory and the oldest are evicted at capacity.
    """
    return AnalysisJobResponse.from_job(service.get(analysis_id))
