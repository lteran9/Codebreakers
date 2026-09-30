"""Request body size enforcement."""

from http import HTTPStatus

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codebreakers.api.problems import problem_response


class RequestSizeLimitMiddleware:
    """Reject request bodies larger than ``max_body_bytes`` with 413."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and (
            not declared.isdigit() or int(declared) > self.max_body_bytes
        ):
            await self._reject(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # Starlette HTTPException passes through FastAPI's body parser.
                    raise HTTPException(413, self._detail)
            return message

        await self.app(scope, limited_receive, send)

    @property
    def _detail(self) -> str:
        return f"Request body must not exceed {self.max_body_bytes} bytes."

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = problem_response(
            scope,
            413,
            "payload-too-large",
            HTTPStatus(413).phrase,
            self._detail,
        )
        await response(scope, receive, send)
