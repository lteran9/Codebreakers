"""Analysis job routes."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from codebreakers.api.dependencies import AnalysisServiceDep
from codebreakers.api.problems import problem_responses
from codebreakers.api.schemas import (
    AnalysisJobPageResponse,
    AnalysisJobResponse,
    AnalysisRequest,
)

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
    status_code=status.HTTP_201_CREATED,
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
    """Run a keyless analysis and return the resulting job resource.

    Small analyses currently complete synchronously, so the job is returned in
    a terminal state. Clients should still inspect `status`, because queued
    execution will later return non-terminal jobs.
    """
    job = service.submit(body.analyzer, body.text, body.language)
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
    """Return a previously submitted analysis job.

    Jobs are held in process memory until durable persistence is added, so
    they are lost on restart and the oldest are evicted at capacity.
    """
    return AnalysisJobResponse.from_job(service.get(analysis_id))
