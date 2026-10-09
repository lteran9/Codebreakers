"""Smoke-test a running Codebreakers stack over HTTP (standard library only).

Exercises health probes, a synchronous cipher call, and the asynchronous
analysis path end to end: API -> outbox -> relay -> queue -> worker -> result.

    python scripts/smoke_test.py --base-url http://127.0.0.1:8000
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

PLAINTEXT = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG WHILE THE BAND PLAYS ON"
SHIFT = "3"
TERMINAL = {"succeeded", "failed", "cancelled"}
RATE_LIMIT_RETRIES = 5


class SmokeTestError(Exception):
    """A smoke-test expectation was not met."""


def _request(
    base_url: str, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, str], Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        urljoin(base_url, path),
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    for _ in range(RATE_LIMIT_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if error.code != 429:
                raise SmokeTestError(
                    f"{method} {path} -> {error.code}: {detail}"
                ) from error
            # The API rate-limits per client; honour its back-off and retry.
            time.sleep(min(float(error.headers.get("Retry-After") or 1), 30.0))
    raise SmokeTestError(f"{method} {path} stayed rate-limited")


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def _wait_until_ready(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            _request(base_url, "GET", "/health/ready")
            return
        except (SmokeTestError, OSError) as error:
            if time.monotonic() >= deadline:
                raise SmokeTestError(f"API never became ready: {error}") from error
            time.sleep(1)


def run(base_url: str, timeout: float) -> None:
    _wait_until_ready(base_url, timeout)
    status, _, _ = _request(base_url, "GET", "/health/live")
    _expect(status == 200, f"liveness returned {status}")
    print("ok   health probes")

    _, _, encrypted = _request(
        base_url,
        "POST",
        "/v1/ciphers/caesar/encrypt",
        {"text": PLAINTEXT, "key": SHIFT},
    )
    ciphertext = encrypted["result"]
    _expect(ciphertext != PLAINTEXT, "encryption returned the plaintext")
    print("ok   synchronous caesar encryption")

    status, headers, job = _request(
        base_url,
        "POST",
        "/v1/analyses",
        {"analyzer": "caesar-bruteforce", "text": ciphertext, "language": "english"},
    )
    _expect(status == 202, f"analysis submission returned {status}")
    location = headers.get("Location") or headers.get("location")
    _expect(bool(location), "analysis submission returned no Location header")
    print(f"ok   analysis {job['id']} accepted")

    deadline = time.monotonic() + timeout
    while job["status"] not in TERMINAL:
        _expect(time.monotonic() < deadline, f"analysis still {job['status']}")
        time.sleep(1)
        _, _, job = _request(base_url, "GET", str(location))

    _expect(job["status"] == "succeeded", f"analysis ended as {job['status']}")
    best = job["result"]["candidates"][0]
    _expect(best["key"] == SHIFT, f"top candidate key was {best['key']!r}")
    _expect(best["text"] == PLAINTEXT, "top candidate did not recover the plaintext")
    print("ok   asynchronous analysis completed by the worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        run(args.base_url, args.timeout)
    except SmokeTestError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
