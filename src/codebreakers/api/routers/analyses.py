"""Analysis job routes."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from codebreakers.api.correlation import correlation_id_from_scope
from codebreakers.api.dependencies import AnalysisServiceDep
from codebreakers.api.problems import problem_responses
from codebreakers.api.schemas import (
    AnalysisJobPageResponse,
    AnalysisJobResponse,
    AnalysisRequest,
)
from codebreakers.application.messaging import TraceContext

TRACEPARENT_HEADER = "traceparent"

router = APIRouter(prefix="/analyses", tags=["analyses"])


@router.get(
    "",
    operation_id="list_analyses",
    summary="List analyses",
    responses=problem_responses(422),
)
def list_analyses(
    service: AnalysisServiceDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> AnalysisJobPageResponse:
    """Return retained jobs newest first using offset and limit pagination."""
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
    responses=problem_responses(413, 422),
)
def create_analysis(
    body: AnalysisRequest,
    request: Request,
    response: Response,
    service: AnalysisServiceDep,
) -> AnalysisJobResponse:
    """Queue a keyless analysis and return the pending job resource.

    A worker runs the analysis asynchronously. Poll the `Location` URL until
    `status` is `succeeded`, `failed`, or `cancelled`. A W3C `traceparent`
    header, when valid, is propagated to the worker with the correlation ID.
    """
    trace = TraceContext.sanitized(
        correlation_id_from_scope(request.scope),
        request.headers.get(TRACEPARENT_HEADER),
    )
    job = service.submit(body.analyzer, body.text, body.language, trace)
    response.headers["Location"] = request.app.url_path_for(
        "get_analysis", analysis_id=str(job.id)
    )
    return AnalysisJobResponse.from_job(job)


@router.get(
    "/{analysis_id}",
    operation_id="get_analysis",
    summary="Get an analysis",
    responses=problem_responses(404, 422),
)
def get_analysis(analysis_id: UUID, service: AnalysisServiceDep) -> AnalysisJobResponse:
    """Return a previously submitted analysis job and its current status.

    Jobs are durable when PostgreSQL is configured; otherwise they are held in
    process memory and the oldest are evicted at capacity.
    """
    return AnalysisJobResponse.from_job(service.get(analysis_id))
