"""Correlation ID propagation for HTTP requests."""

import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codebreakers.infrastructure.structured_logging import bind_log_context

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

logger = logging.getLogger("codebreakers.api")


def resolve_correlation_id(inbound: str | None) -> str:
    """Reuse a well-formed inbound request ID, otherwise generate a new one."""
    if inbound is not None and _VALID_REQUEST_ID.fullmatch(inbound):
        return inbound
    return str(uuid4())


def correlation_id_from_scope(scope: Scope) -> str:
    """Return the correlation ID assigned to this request."""
    state = scope.get("state") or {}
    correlation_id = state.get("correlation_id")
    return correlation_id if isinstance(correlation_id, str) else str(uuid4())


class CorrelationIdMiddleware:
    """Assign a correlation ID, echo it in responses, and log request metadata.

    Request bodies are never logged so plaintext, ciphertext, and keys stay out
    of routine logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = resolve_correlation_id(
            Headers(scope=scope).get(REQUEST_ID_HEADER)
        )
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        status_code = 500
        started = time.perf_counter()

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, correlation_id)
            await send(message)

        try:
            with bind_log_context(correlation_id=correlation_id):
                await self.app(scope, receive, send_with_header)
        finally:
            logger.info(
                "request_completed method=%s path=%r status=%d duration_ms=%.1f "
                "correlation_id=%s",
                scope["method"],
                scope["path"],
                status_code,
                (time.perf_counter() - started) * 1000,
                correlation_id,
            )
