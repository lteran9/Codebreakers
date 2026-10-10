"""Runtime abuse controls: rate limits, job backlog cap, and listing toggle."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import Scope

from codebreakers.api import ApiSettings, create_app
from codebreakers.api.rate_limit import RateLimiter, client_address
from codebreakers.application.analysis import AnalysisOutcome, AnalysisStatus
from codebreakers.application.errors import AnalysisCapacityError
from codebreakers.application.processing import ExecutionBudget
from codebreakers.composition import create_analysis_service
from codebreakers.infrastructure.persistence.memory import InMemoryAnalysisRepository
from codebreakers.worker.settings import (
    ConfigurationError,
    QueueBackend,
    WorkerSettings,
)

PROBLEM_JSON = "application/problem+json"
ANALYSIS = {
    "analyzer": "caesar-bruteforce",
    "text": "KHOOR ZRUOG",
    "language": "english",
}
ENCRYPT = {"text": "HELLO", "key": "3"}


def _client(**overrides: Any) -> TestClient:
    # A distributed backend only records jobs, so submissions stay pending.
    settings = ApiSettings(
        worker=WorkerSettings(queue_backend=QueueBackend.POSTGRES),
        **overrides,
    )
    service = create_analysis_service(
        InMemoryAnalysisRepository(), max_unfinished=settings.max_pending_analyses
    )
    return TestClient(
        create_app(
            settings, analysis_service=service, readiness_checks={"ok": lambda: True}
        ),
        raise_server_exceptions=False,
    )


@pytest.fixture(autouse=True)
def _database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Required by distributed backends; never connected because a service is injected.
    monkeypatch.setenv("CODEBREAKERS_DATABASE_URL", "postgresql://unused/db")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _client(rate_limit_per_minute=3, analysis_submissions_per_minute=0) as c:
        yield c


# --- Token buckets ------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.unit
def test_bucket_allows_a_burst_then_refills_continuously() -> None:
    clock = Clock()
    limiter = RateLimiter(60, clock=clock)

    assert all(limiter.acquire("a") is None for _ in range(60))
    assert limiter.acquire("a") == pytest.approx(1.0)
    assert limiter.acquire("b") is None

    clock.now = 0.5
    assert limiter.acquire("a") == pytest.approx(0.5)
    clock.now = 1.0
    assert limiter.acquire("a") is None


@pytest.mark.unit
def test_bucket_memory_is_bounded_by_evicting_the_least_recent_client() -> None:
    limiter = RateLimiter(1, max_clients=2)
    limiter.acquire("a")
    limiter.acquire("b")
    limiter.acquire("a")
    limiter.acquire("c")  # evicts "b", the least recently seen

    assert limiter.acquire("a") is not None
    assert limiter.acquire("b") is None


@pytest.mark.unit
@pytest.mark.parametrize(("per_minute", "max_clients"), [(0, 1), (1, 0)])
def test_bucket_rejects_non_positive_settings(
    per_minute: int, max_clients: int
) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimiter(per_minute, max_clients=max_clients)


def _scope(*forwarded: str, peer: tuple[str, int] | None = ("10.0.0.9", 1)) -> Scope:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return {"type": "http", "client": peer, "headers": headers}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("hops", "forwarded", "expected"),
    [
        (0, ("1.1.1.1",), "10.0.0.9"),
        (1, (), "10.0.0.9"),
        (1, ("6.6.6.6, 1.1.1.1",), "1.1.1.1"),
        (1, ("6.6.6.6", "1.1.1.1"), "1.1.1.1"),
        (2, ("6.6.6.6, 1.1.1.1, 172.16.0.1",), "1.1.1.1"),
        (3, ("1.1.1.1",), "1.1.1.1"),
    ],
)
def test_client_address_trusts_only_proxy_appended_entries(
    hops: int, forwarded: tuple[str, ...], expected: str
) -> None:
    assert client_address(_scope(*forwarded), hops) == expected


@pytest.mark.unit
def test_client_address_without_a_peer_is_unknown() -> None:
    assert client_address(_scope(peer=None), 0) == "unknown"


# --- Middleware ---------------------------------------------------------------


@pytest.mark.unit
def test_requests_over_the_limit_get_problem_429_with_retry_after(
    client: TestClient,
) -> None:
    for _ in range(3):
        assert (
            client.post("/v1/ciphers/caesar/encrypt", json=ENCRYPT).status_code == 200
        )

    response = client.post("/v1/ciphers/caesar/encrypt", json=ENCRYPT)

    assert response.status_code == 429
    assert response.headers["content-type"] == PROBLEM_JSON
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["x-request-id"]
    assert response.json()["code"] == "rate-limited"


@pytest.mark.unit
def test_health_probes_are_never_rate_limited(client: TestClient) -> None:
    for _ in range(10):
        assert client.get("/health/live").status_code == 200


@pytest.mark.unit
def test_limits_are_per_forwarded_client_when_a_proxy_is_trusted() -> None:
    with _client(rate_limit_per_minute=1, trusted_proxy_hops=1) as c:
        first = {"X-Forwarded-For": "6.6.6.6, 1.1.1.1"}
        spoofed = {"X-Forwarded-For": "7.7.7.7, 1.1.1.1"}
        other = {"X-Forwarded-For": "2.2.2.2"}
        assert c.get("/v1/analyses", headers=first).status_code == 200
        assert c.get("/v1/analyses", headers=spoofed).status_code == 429
        assert c.get("/v1/analyses", headers=other).status_code == 200


@pytest.mark.unit
def test_submissions_have_a_stricter_limit_than_reads() -> None:
    with _client(rate_limit_per_minute=100, analysis_submissions_per_minute=2) as c:
        assert [
            c.post("/v1/analyses", json=ANALYSIS).status_code for _ in range(3)
        ] == [
            202,
            202,
            429,
        ]
        assert c.get("/v1/analyses").status_code == 200


@pytest.mark.unit
def test_zero_disables_rate_limits() -> None:
    with _client(rate_limit_per_minute=0, analysis_submissions_per_minute=0) as c:
        codes = {c.post("/v1/analyses", json=ANALYSIS).status_code for _ in range(30)}
    assert codes == {202}


# --- Workload limits ----------------------------------------------------------


@pytest.mark.unit
def test_a_full_backlog_answers_503_with_retry_after() -> None:
    with _client(max_pending_analyses=2, analysis_submissions_per_minute=0) as c:
        assert c.post("/v1/analyses", json=ANALYSIS).status_code == 202
        assert c.post("/v1/analyses", json=ANALYSIS).status_code == 202
        response = c.post("/v1/analyses", json=ANALYSIS)

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["retry-after"] == "30"
    assert response.json()["code"] == "analysis-capacity-exceeded"


@pytest.mark.unit
def test_finished_jobs_do_not_count_towards_the_backlog() -> None:
    repository = InMemoryAnalysisRepository()
    service = create_analysis_service(repository, max_unfinished=1)
    job = service.submit("caesar-bruteforce", "KHOOR", "english")
    with pytest.raises(AnalysisCapacityError):
        service.submit("caesar-bruteforce", "KHOOR", "english")

    now = datetime.now(UTC)
    running = repository.update(job.claim(now, timedelta(minutes=1)), job.version)
    assert repository.count_unfinished() == 1
    repository.update(
        running.transition(AnalysisStatus.CANCELLED, now), running.version
    )
    assert repository.count_unfinished() == 0
    service.submit("caesar-bruteforce", "KHOOR", "english")


class BlockingExecutor:
    def __init__(self) -> None:
        self.release = threading.Event()

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisOutcome:
        self.release.wait(10)
        raise RuntimeError("released")


@pytest.mark.unit
def test_the_app_applies_the_backlog_cap_to_its_own_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEBREAKERS_DATABASE_URL")
    executor = BlockingExecutor()
    settings = ApiSettings(worker=WorkerSettings(), max_pending_analyses=1)
    with TestClient(create_app(settings, executor=executor)) as c:
        try:
            first = c.post("/v1/analyses", json=ANALYSIS).status_code
            second = c.post("/v1/analyses", json=ANALYSIS).status_code
        finally:
            executor.release.set()
    assert (first, second) == (202, 503)


@pytest.mark.unit
def test_the_backlog_cap_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        create_analysis_service(max_unfinished=0)


# --- Listing toggle -----------------------------------------------------------


@pytest.mark.unit
def test_listing_can_be_disabled_while_jobs_stay_reachable_by_id() -> None:
    with _client(analysis_listing_enabled=False) as c:
        created = c.post("/v1/analyses", json=ANALYSIS)
        listing = c.get("/v1/analyses")
        fetched = c.get(created.headers["location"])

    assert listing.status_code == 404
    assert listing.headers["content-type"] == PROBLEM_JSON
    assert fetched.status_code == 200


# --- Environment configuration ------------------------------------------------


@pytest.mark.unit
def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEBREAKERS_RATE_LIMIT_PER_MINUTE", "50")
    monkeypatch.setenv("CODEBREAKERS_ANALYSIS_SUBMISSIONS_PER_MINUTE", "0")
    monkeypatch.setenv("CODEBREAKERS_TRUSTED_PROXY_HOPS", " 1 ")
    monkeypatch.setenv("CODEBREAKERS_MAX_PENDING_ANALYSES", "")
    monkeypatch.setenv("CODEBREAKERS_ANALYSIS_LISTING_ENABLED", "False")

    settings = ApiSettings(worker=WorkerSettings())

    assert settings.rate_limit_per_minute == 50
    assert settings.analysis_submissions_per_minute == 0
    assert settings.trusted_proxy_hops == 1
    assert settings.max_pending_analyses == 100
    assert settings.analysis_listing_enabled is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CODEBREAKERS_RATE_LIMIT_PER_MINUTE", "-1"),
        ("CODEBREAKERS_TRUSTED_PROXY_HOPS", "one"),
        ("CODEBREAKERS_ANALYSIS_LISTING_ENABLED", "maybe"),
    ],
)
def test_invalid_settings_are_configuration_errors(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        ApiSettings(worker=WorkerSettings())
