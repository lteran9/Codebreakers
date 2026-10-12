"""Measure a running Codebreakers API: latency and queued-job throughput.

Small, bounded, and standard library only. It stays inside the API's default
per-client limits (120 requests and 10 analysis submissions a minute) and
retries politely on 429. Output is JSON and never includes the base URL:

    python scripts/measure_api.py --base-url http://127.0.0.1:8000
    make azure-measure                      # against the deployed dev API

Phases:

1. ``first_response_seconds``: time to the first ``/health/live`` response. On
   a scaled-to-zero deployment this includes the replica cold start.
2. ``encrypt_latency_ms``: client-observed latency of sequential synchronous
   Caesar encryptions (network included).
3. ``analysis_batches``: batches of Caesar analyses submitted back to back.
   Durations use the server's ``created_at`` and ``completed_at`` timestamps, so
   polling frequency does not affect them. The first batch after an idle
   period includes relay and worker cold starts.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

PLAINTEXT = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG WHILE THE BAND PLAYS ON"
CIPHERTEXT = "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ ZKLOH WKH EDQG SODBV RQ"
TERMINAL = {"succeeded", "failed", "cancelled"}
POLL_INTERVAL_SECONDS = 2.0
RATE_LIMIT_RETRIES = 10


class MeasurementError(Exception):
    """The API did not behave as the measurement expects."""


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Distribution of client-observed latencies in milliseconds."""

    samples: int
    p50: float
    p95: float
    max: float


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """Server-side timing of one batch of analysis jobs."""

    jobs: int
    succeeded: int
    median_queue_to_completion_seconds: float
    max_queue_to_completion_seconds: float
    batch_wall_seconds: float
    jobs_per_minute: float


@dataclass(frozen=True, slots=True)
class Measurement:
    """Everything one run of this script measured."""

    measured_at: str
    first_response_seconds: float
    encrypt_latency_ms: LatencySummary
    submit_latency_ms: LatencySummary
    analysis_batches: tuple[BatchSummary, ...]


def _request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[float, dict[str, str], Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        urljoin(base_url, path),
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    for _ in range(RATE_LIMIT_RETRIES):
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
                return time.perf_counter() - start, dict(response.headers), payload
        except urllib.error.HTTPError as error:
            if error.code != 429:
                detail = error.read().decode(errors="replace")
                raise MeasurementError(
                    f"{method} {path} -> {error.code}: {detail}"
                ) from error
            time.sleep(min(float(error.headers.get("Retry-After") or 1), 60.0))
    raise MeasurementError(f"{method} {path} stayed rate-limited")


def _summarize(seconds: list[float]) -> LatencySummary:
    millis = sorted(value * 1000 for value in seconds)
    p95_index = max(0, round(0.95 * len(millis)) - 1)
    return LatencySummary(
        samples=len(millis),
        p50=round(statistics.median(millis), 1),
        p95=round(millis[p95_index], 1),
        max=round(millis[-1], 1),
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _first_response(base_url: str, timeout: float) -> float:
    start = time.perf_counter()
    deadline = start + timeout
    while True:
        try:
            _request(base_url, "GET", "/health/live", timeout=timeout)
            return time.perf_counter() - start
        except (MeasurementError, OSError) as error:
            if time.perf_counter() >= deadline:
                raise MeasurementError(f"API never responded: {error}") from error
            time.sleep(1)


def _run_batch(
    base_url: str, size: int, timeout: float, submit_latencies: list[float]
) -> BatchSummary:
    locations: list[str] = []
    for _ in range(size):
        elapsed, headers, _ = _request(
            base_url,
            "POST",
            "/v1/analyses",
            {"analyzer": "caesar-bruteforce", "text": CIPHERTEXT},
        )
        submit_latencies.append(elapsed)
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise MeasurementError("analysis submission returned no Location header")
        locations.append(location)

    deadline = time.monotonic() + timeout
    finished: dict[str, Any] = {}
    while len(finished) < size:
        if time.monotonic() >= deadline:
            raise MeasurementError(f"{size - len(finished)} analyses never finished")
        # Poll only the oldest unfinished job to stay well under the rate limit.
        location = next(item for item in locations if item not in finished)
        _, _, job = _request(base_url, "GET", location)
        if job["status"] in TERMINAL:
            finished[location] = job
        else:
            time.sleep(POLL_INTERVAL_SECONDS)

    jobs = list(finished.values())
    created = [_parse_time(job["created_at"]) for job in jobs]
    completed = [_parse_time(job["completed_at"]) for job in jobs]
    durations = [
        (done - start).total_seconds()
        for start, done in zip(created, completed, strict=True)
    ]
    wall = (max(completed) - min(created)).total_seconds()
    return BatchSummary(
        jobs=size,
        succeeded=sum(job["status"] == "succeeded" for job in jobs),
        median_queue_to_completion_seconds=round(statistics.median(durations), 2),
        max_queue_to_completion_seconds=round(max(durations), 2),
        batch_wall_seconds=round(wall, 2),
        jobs_per_minute=round(size / wall * 60, 1) if wall > 0 else float("inf"),
    )


def run(
    base_url: str, requests: int, batches: int, batch_size: int, timeout: float
) -> Measurement:
    """Run every measurement phase against ``base_url``."""
    measured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    first_response = _first_response(base_url, timeout)

    encrypt_latencies: list[float] = []
    for _ in range(requests):
        elapsed, _, body = _request(
            base_url,
            "POST",
            "/v1/ciphers/caesar/encrypt",
            {"text": PLAINTEXT, "key": "3"},
        )
        if body["result"] != CIPHERTEXT:
            raise MeasurementError("encryption returned an unexpected result")
        encrypt_latencies.append(elapsed)

    submit_latencies: list[float] = []
    summaries = tuple(
        _run_batch(base_url, batch_size, timeout, submit_latencies)
        for _ in range(batches)
    )
    return Measurement(
        measured_at=measured_at,
        first_response_seconds=round(first_response, 2),
        encrypt_latency_ms=_summarize(encrypt_latencies),
        submit_latency_ms=_summarize(submit_latencies),
        analysis_batches=summaries,
    )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the measurement, and print JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    try:
        measurement = run(
            args.base_url, args.requests, args.batches, args.batch_size, args.timeout
        )
    except MeasurementError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(measurement), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
