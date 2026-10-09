"""Per-client request rate limiting.

Buckets live in process memory, so each API replica enforces its own limit:
with ``n`` replicas a client can reach up to ``n`` times the configured rate.
That is enough to blunt a single noisy client on a small deployment; a shared
store or an edge gateway is needed for an exact global limit (see the threat
model).
"""

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from codebreakers.api.problems import problem_response

FORWARDED_FOR_HEADER = "x-forwarded-for"
UNKNOWN_CLIENT = "unknown"


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token buckets keyed by client, refilled continuously.

    Each client may burst ``per_minute`` requests, then sustain ``per_minute``
    per minute. Only the ``max_clients`` most recently seen clients are
    tracked, which bounds memory; an evicted client starts with a full bucket.
    """

    def __init__(
        self,
        per_minute: int,
        *,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute < 1 or max_clients < 1:
            msg = "per_minute and max_clients must be at least 1."
            raise ValueError(msg)
        self._capacity = float(per_minute)
        self._refill_per_second = per_minute / 60.0
        self._max_clients = max_clients
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, client: str) -> float | None:
        """Take one token; return ``None`` if allowed, else seconds to wait."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.pop(client, None)
            if bucket is None:
                bucket = _Bucket(self._capacity, now)
            else:
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(
                    self._capacity, bucket.tokens + elapsed * self._refill_per_second
                )
                bucket.updated = now
            self._buckets[client] = bucket
            while len(self._buckets) > self._max_clients:
                self._buckets.popitem(last=False)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            return (1.0 - bucket.tokens) / self._refill_per_second


def client_address(scope: Scope, trusted_proxy_hops: int) -> str:
    """Return the client IP, trusting only the last ``trusted_proxy_hops`` hops.

    Each trusted proxy appends the address it received the request from to
    ``X-Forwarded-For``, so the client is the entry ``trusted_proxy_hops`` from
    the right. Entries further left are client-supplied and can be forged.
    """
    peer = scope.get("client")
    direct = peer[0] if peer else UNKNOWN_CLIENT
    if trusted_proxy_hops <= 0:
        return direct
    forwarded = [
        entry.strip()
        for header in Headers(scope=scope).getlist(FORWARDED_FOR_HEADER)
        for entry in header.split(",")
        if entry.strip()
    ]
    if not forwarded:
        return direct
    return forwarded[-min(trusted_proxy_hops, len(forwarded))]


class RateLimitMiddleware:
    """Reject clients over their request rate with 429 and ``Retry-After``.

    ``general`` applies to every request except health probes; ``submissions``
    additionally limits ``POST`` to ``submission_path``, the expensive call.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        general: RateLimiter | None,
        submissions: RateLimiter | None,
        submission_path: str,
        trusted_proxy_hops: int = 0,
        exempt_prefix: str = "/health",
    ) -> None:
        self.app = app
        self.general = general
        self.submissions = submissions
        self.submission_path = submission_path
        self.trusted_proxy_hops = trusted_proxy_hops
        self.exempt_prefix = exempt_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope.get("path", "")
if scope["type"] != "http" or path in {
            f"{self.exempt_prefix}/live",
            f"{self.exempt_prefix}/ready",
        }:
            await self.app(scope, receive, send)
            return

        client = client_address(scope, self.trusted_proxy_hops)
        wait = self._acquire(self.general, client)
        if wait is None and scope["method"] == "POST" and path == self.submission_path:
            wait = self._acquire(self.submissions, client)
        if wait is None:
            await self.app(scope, receive, send)
            return

        response = problem_response(
            scope,
            HTTPStatus.TOO_MANY_REQUESTS,
            "rate-limited",
            HTTPStatus.TOO_MANY_REQUESTS.phrase,
            "Too many requests from this client. Retry after the indicated delay.",
        )
        response.headers["Retry-After"] = str(max(1, math.ceil(wait)))
        await response(scope, receive, send)

    @staticmethod
    def _acquire(limiter: RateLimiter | None, client: str) -> float | None:
        return None if limiter is None else limiter.acquire(client)
