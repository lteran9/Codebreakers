"""RFC 9457 problem details and exception-to-response mapping."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from codebreakers.api.correlation import REQUEST_ID_HEADER, correlation_id_from_scope
from codebreakers.application.errors import (
    AnalysisNotFoundError,
    UnsupportedAnalyzerError,
    UnsupportedCipherError,
    UnsupportedLanguageError,
)
from codebreakers.domain.errors import (
    AlphabetError,
    CipherKeyError,
    CodebreakersError,
    InsufficientTextError,
    UnknownSymbolError,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_SCHEMA_REF = "#/components/schemas/ProblemDetails"

logger = logging.getLogger("codebreakers.api")

# Ordered most specific first; the first isinstance match wins.
_ERROR_MAPPINGS: tuple[tuple[type[CodebreakersError], int, str, str], ...] = (
    (UnsupportedCipherError, 404, "unsupported-cipher", "Unsupported cipher"),
    (AnalysisNotFoundError, 404, "analysis-not-found", "Analysis not found"),
    (UnsupportedAnalyzerError, 422, "unsupported-analyzer", "Unsupported analyzer"),
    (UnsupportedLanguageError, 422, "unsupported-language", "Unsupported language"),
    (AlphabetError, 422, "invalid-alphabet", "Invalid alphabet"),
    (CipherKeyError, 422, "invalid-key", "Invalid key"),
    (UnknownSymbolError, 422, "unknown-symbol", "Unknown symbol"),
    (InsufficientTextError, 422, "insufficient-text", "Insufficient text"),
)

_HTTP_ERROR_CODES: dict[int, str] = {
    404: "not-found",
    405: "method-not-allowed",
    413: "payload-too-large",
}


class ValidationIssue(BaseModel):
    """A single schema validation failure, without the offending input value."""

    location: list[str | int]
    message: str
    type: str


class ProblemDetails(BaseModel):
    """RFC 9457 problem details with machine-readable extensions."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "urn:codebreakers:problem:invalid-key",
                    "title": "Invalid key",
                    "status": 422,
                    "detail": "Caesar key must be an integer.",
                    "instance": "/v1/ciphers/caesar/encrypt",
                    "code": "invalid-key",
                    "correlation_id": "4f1c2a8e-6c1b-4a51-9d7e-2b0f3f5b8c11",
                }
            ]
        }
    )

    type: str = Field(description="URI reference identifying the problem type.")
    title: str = Field(description="Short, human-readable summary.")
    status: int = Field(description="HTTP status code.")
    detail: str = Field(description="Explanation specific to this occurrence.")
    instance: str = Field(description="Request path that produced the problem.")
    code: str = Field(description="Stable machine-readable error code.")
    correlation_id: str = Field(description="Matches the X-Request-ID header.")
    errors: list[ValidationIssue] | None = Field(
        default=None, description="Present for schema validation failures."
    )


def problem_response(
    scope: Scope,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: list[ValidationIssue] | None = None,
) -> JSONResponse:
    """Build a problem+json response tagged with the request correlation ID."""
    correlation_id = correlation_id_from_scope(scope)
    problem = ProblemDetails(
        type=f"urn:codebreakers:problem:{code}",
        title=title,
        status=status,
        detail=detail,
        instance=scope.get("path", ""),
        code=code,
        correlation_id=correlation_id,
        errors=errors,
    )
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={REQUEST_ID_HEADER: correlation_id},
    )


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI response declarations for problem+json statuses."""
    return {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": PROBLEM_SCHEMA_REF}}},
        }
        for status in statuses
    }


def _handle_codebreakers_error(request: Request, exc: Exception) -> JSONResponse:
    for error_type, status, code, title in _ERROR_MAPPINGS:
        if isinstance(exc, error_type):
            return problem_response(request.scope, status, code, title, str(exc))
    return problem_response(
        request.scope, 422, "invalid-request", "Invalid request", str(exc)
    )


def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Drop "input" and "ctx" so submitted text and keys are never echoed back.
    issues = [
        ValidationIssue(
            location=list(error["loc"]), message=error["msg"], type=error["type"]
        )
        for error in exc.errors()
    ]
    return problem_response(
        request.scope,
        422,
        "validation-error",
        "Request validation failed",
        "The request does not match the documented schema.",
        errors=issues,
    )


def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    phrase = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if isinstance(exc.detail, str) else phrase
    return problem_response(
        request.scope,
        exc.status_code,
        _HTTP_ERROR_CODES.get(exc.status_code, "http-error"),
        phrase,
        detail,
    )


def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception type=%s correlation_id=%s",
        type(exc).__name__,
        correlation_id_from_scope(request.scope),
    )
    return problem_response(
        request.scope,
        500,
        "internal-error",
        "Internal server error",
        "An unexpected error occurred. Quote the correlation ID when reporting it.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Map every error path to a consistent problem response."""
    app.add_exception_handler(CodebreakersError, _handle_codebreakers_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
