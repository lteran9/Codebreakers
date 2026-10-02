"""Contract tests for the HTTP API using FastAPI's test client."""

import logging
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from codebreakers.api import ApiSettings, create_app
from codebreakers.api.dependencies import get_cipher_runner
from codebreakers.application.errors import UnsupportedCipherError
from codebreakers.cli.main import app as cli_app
from codebreakers.domain.errors import (
    AmbiguousKeyError,
    CodebreakersError,
    DomainError,
    DuplicateSymbolError,
    InsufficientTextError,
    InvalidKeyError,
    UnknownSymbolError,
)
from codebreakers.worker.execution import InlineAnalysisExecutor
from codebreakers.worker.settings import (
    ConfigurationError,
    QueueBackend,
    WorkerSettings,
)

PROBLEM_JSON = "application/problem+json"
CAESAR_CIPHERTEXT = (
    "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ DQG NHHSV UXQQLQJ WKURXJK WKH ILHOG"
)
CAESAR_PLAINTEXT = (
    "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG AND KEEPS RUNNING THROUGH THE FIELD"
)


TERMINAL = {"succeeded", "failed", "cancelled"}


def _local_app() -> Any:
    """Build an app whose in-process worker runs analyzers inline for speed."""
    return create_app(
        ApiSettings(worker=WorkerSettings()), executor=InlineAnalysisExecutor()
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_local_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _wait_for_terminal(
    client: TestClient, location: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        job: dict[str, Any] = client.get(location).json()
        if job["status"] in TERMINAL or time.monotonic() > deadline:
            return job
        time.sleep(0.02)


def _submit(client: TestClient, body: dict[str, str]) -> dict[str, Any]:
    created = client.post("/v1/analyses", json=body)
    assert created.status_code == 202
    return _wait_for_terminal(client, created.headers["location"])


def _assert_problem(response_json: dict[str, object], status: int, code: str) -> None:
    assert response_json["status"] == status
    assert response_json["code"] == code
    assert response_json["type"] == f"urn:codebreakers:problem:{code}"
    for field in ("title", "detail", "instance", "correlation_id"):
        assert isinstance(response_json[field], str)


# --- Cipher operations -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cipher", "key", "plaintext", "ciphertext"),
    [
        ("caesar", "3", "HELLO WORLD", "KHOOR ZRUOG"),
        ("vigenere", "LEMON", "ATTACK AT DAWN", "LXFOPV EF RNHR"),
        ("substitution", "QWERTYUIOPASDFGHJKLZXCVBNM", "ATTACK", "QZZQEA"),
    ],
)
def test_encrypt_and_decrypt_round_trip(
    client: TestClient, cipher: str, key: str, plaintext: str, ciphertext: str
) -> None:
    encrypted = client.post(
        f"/v1/ciphers/{cipher}/encrypt", json={"text": plaintext, "key": key}
    )
    assert encrypted.status_code == 200
    assert encrypted.json() == {
        "cipher": cipher,
        "operation": "encrypt",
        "result": ciphertext,
    }

    decrypted = client.post(
        f"/v1/ciphers/{cipher}/decrypt", json={"text": ciphertext, "key": key}
    )
    assert decrypted.status_code == 200
    assert decrypted.json()["result"] == plaintext


@pytest.mark.unit
def test_homophonic_round_trip(client: TestClient) -> None:
    key = '{"A":["11","12"],"B":["21"]}'
    encrypted = client.post(
        "/v1/ciphers/homophonic/encrypt",
        json={"text": "ABBA", "key": key, "alphabet": "AB"},
    )
    assert encrypted.status_code == 200
    decrypted = client.post(
        "/v1/ciphers/homophonic/decrypt",
        json={"text": encrypted.json()["result"], "key": key, "alphabet": "AB"},
    )
    assert decrypted.json()["result"] == "ABBA"


@pytest.mark.unit
def test_custom_alphabet(client: TestClient) -> None:
    response = client.post(
        "/v1/ciphers/caesar/encrypt",
        json={"text": "1239", "key": "2", "alphabet": "0123456789"},
    )
    assert response.json()["result"] == "3451"


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_api_matches_cli(client: TestClient, operation: str) -> None:
    args = ["--cipher", "vigenere", "--key", "LEMON", "--text", "Attack at dawn!"]
    cli_result = CliRunner().invoke(cli_app, [operation, *args])
    api_result = client.post(
        f"/v1/ciphers/vigenere/{operation}",
        json={"text": "Attack at dawn!", "key": "LEMON"},
    )
    assert cli_result.exit_code == 0
    assert api_result.json()["result"] == cli_result.stdout.rstrip("\n")


@pytest.mark.unit
def test_unsupported_cipher_returns_404_problem(client: TestClient) -> None:
    response = client.post("/v1/ciphers/rot47/encrypt", json={"text": "HI", "key": "3"})
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    _assert_problem(response.json(), 404, "unsupported-cipher")
    assert "caesar" in response.json()["detail"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"text": "HI", "key": "abc"}, "invalid-key"),
        ({"text": "HI", "key": "3", "alphabet": "AAB"}, "invalid-alphabet"),
        ({"text": "HI", "key": "3", "alphabet": ""}, "invalid-alphabet"),
    ],
)
def test_domain_validation_errors_return_422(
    client: TestClient, body: dict[str, str], code: str
) -> None:
    response = client.post("/v1/ciphers/caesar/encrypt", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    _assert_problem(response.json(), 422, code)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (UnsupportedCipherError("x", ["caesar"]), 404, "unsupported-cipher"),
        (InvalidKeyError("bad key"), 422, "invalid-key"),
        (AmbiguousKeyError("ambiguous"), 422, "invalid-key"),
        (DuplicateSymbolError("dup"), 422, "invalid-alphabet"),
        (UnknownSymbolError("unknown"), 422, "unknown-symbol"),
        (InsufficientTextError("short"), 422, "insufficient-text"),
        (DomainError("generic"), 422, "invalid-request"),
        (CodebreakersError("base"), 422, "invalid-request"),
    ],
)
def test_domain_exceptions_map_to_intentional_4xx(
    error: Exception, status: int, code: str
) -> None:
    app = create_app()

    def failing_runner(*_args: object) -> str:
        raise error

    app.dependency_overrides[get_cipher_runner] = lambda: failing_runner
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/ciphers/caesar/encrypt", json={"text": "HI", "key": "3"}
        )
    assert response.status_code == status
    _assert_problem(response.json(), status, code)
    assert response.json()["detail"] == str(error)


@pytest.mark.unit
def test_unexpected_failure_returns_sanitized_500() -> None:
    app = create_app()

    def exploding_runner(*_args: object) -> str:
        raise RuntimeError("internal secret detail")

    app.dependency_overrides[get_cipher_runner] = lambda: exploding_runner
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/ciphers/caesar/encrypt",
            json={"text": "HI", "key": "3"},
            headers={"X-Request-ID": "trace-500"},
        )
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.headers["x-request-id"] == "trace-500"
    body = response.json()
    _assert_problem(body, 500, "internal-error")
    assert body["correlation_id"] == "trace-500"
    assert "internal secret detail" not in response.text
    assert "Traceback" not in response.text


# --- Schema validation -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "location", "error_type"),
    [
        ({"text": "HI"}, ["body", "key"], "missing"),
        ({"text": "HI", "key": "3", "extra": 1}, ["body", "extra"], "extra_forbidden"),
        ({"text": "HI", "key": 3}, ["body", "key"], "string_type"),
        ({"text": "HI", "key": ""}, ["body", "key"], "string_too_short"),
        ({"text": "A" * 20_001, "key": "3"}, ["body", "text"], "string_too_long"),
        ({"text": "HI", "key": "K" * 4_097}, ["body", "key"], "string_too_long"),
    ],
)
def test_schema_validation_errors(
    client: TestClient,
    body: dict[str, object],
    location: list[str],
    error_type: str,
) -> None:
    response = client.post("/v1/ciphers/caesar/encrypt", json=body)
    assert response.status_code == 422
    problem = response.json()
    _assert_problem(problem, 422, "validation-error")
    assert problem["errors"][0]["location"] == location
    assert problem["errors"][0]["type"] == error_type
    assert set(problem["errors"][0]) == {"location", "message", "type"}


@pytest.mark.unit
def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    response = client.post(
        "/v1/ciphers/caesar/encrypt",
        json={"text": "TOPSECRETPLAINTEXT", "key": 12345, "unexpected": "LEAKME"},
    )
    assert response.status_code == 422
    assert "TOPSECRETPLAINTEXT" not in response.text
    assert "12345" not in response.text
    assert "LEAKME" not in response.text


@pytest.mark.unit
def test_malformed_json_is_a_validation_problem(client: TestClient) -> None:
    response = client.post(
        "/v1/ciphers/caesar/encrypt",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    _assert_problem(response.json(), 422, "validation-error")


# --- Payload limits ----------------------------------------------------------


@pytest.mark.unit
def test_declared_oversized_body_returns_413(client: TestClient) -> None:
    response = client.post(
        "/v1/ciphers/caesar/encrypt",
        content=b"x" * 65_537,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "x-request-id" in response.headers
    _assert_problem(response.json(), 413, "payload-too-large")


@pytest.mark.unit
def test_streamed_oversized_body_returns_413(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b"x" * 8_000

    response = client.post(
        "/v1/ciphers/caesar/encrypt",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    _assert_problem(response.json(), 413, "payload-too-large")


@pytest.mark.unit
def test_body_limit_is_configurable() -> None:
    with TestClient(create_app(ApiSettings(max_body_bytes=32))) as client:
        response = client.post(
            "/v1/ciphers/caesar/encrypt",
            json={"text": "HELLO WORLD HELLO WORLD", "key": "3"},
        )
    assert response.status_code == 413


# --- Correlation IDs and generic HTTP errors --------------------------------


@pytest.mark.unit
def test_valid_inbound_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123.x_y"})
    assert response.headers["x-request-id"] == "abc-123.x_y"


@pytest.mark.unit
@pytest.mark.parametrize("inbound", ["has spaces", "x" * 129, "-leading-dash", ""])
def test_invalid_inbound_request_id_is_replaced(
    client: TestClient, inbound: str
) -> None:
    response = client.get("/v1/unknown", headers={"X-Request-ID": inbound})
    generated = response.headers["x-request-id"]
    assert generated != inbound
    assert response.json()["correlation_id"] == generated


@pytest.mark.unit
def test_unknown_route_returns_problem(client: TestClient) -> None:
    response = client.get("/v1/unknown")
    assert response.status_code == 404
    _assert_problem(response.json(), 404, "not-found")


@pytest.mark.unit
def test_wrong_method_returns_problem(client: TestClient) -> None:
    response = client.get("/v1/ciphers/caesar/encrypt")
    assert response.status_code == 405
    _assert_problem(response.json(), 405, "method-not-allowed")


# --- Analyses ----------------------------------------------------------------


@pytest.mark.unit
def test_submit_is_accepted_then_polled_to_completion(client: TestClient) -> None:
    created = client.post(
        "/v1/analyses",
        json={"analyzer": "caesar-bruteforce", "text": CAESAR_CIPHERTEXT},
    )
    assert created.status_code == 202
    accepted = created.json()
    assert accepted["status"] == "pending"
    assert accepted["version"] == 1
    assert accepted["result"] is None
    assert accepted["completed_at"] is None
    assert accepted["language"] == "english"
    assert created.headers["location"] == f"/v1/analyses/{accepted['id']}"

    job = _wait_for_terminal(client, created.headers["location"])
    assert job["status"] == "succeeded"
    assert job["version"] == 3
    assert job["error_code"] is None
    top = job["result"]["candidates"][0]
    assert (top["rank"], top["key"], top["text"]) == (1, "3", CAESAR_PLAINTEXT)


@pytest.mark.unit
def test_submit_without_running_worker_stays_pending() -> None:
    app = _local_app()
    client = TestClient(app)  # no context manager, so the lifespan never starts
    created = client.post(
        "/v1/analyses", json={"analyzer": "caesar-bruteforce", "text": "KHOOR"}
    )
    assert created.status_code == 202
    assert client.get(created.headers["location"]).json()["status"] == "pending"

    app.state.job_runtime.run_until_idle()

    assert client.get(created.headers["location"]).json()["status"] == "succeeded"


@pytest.mark.unit
def test_trace_context_is_propagated_to_the_worker(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="codebreakers.worker")
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    created = client.post(
        "/v1/analyses",
        json={"analyzer": "caesar-bruteforce", "text": CAESAR_CIPHERTEXT},
        headers={"X-Request-ID": "trace-check", "traceparent": traceparent},
    )
    _wait_for_terminal(client, created.headers["location"])

    processed = [r.getMessage() for r in caplog.records if "job_processed" in r.message]
    assert any(
        "correlation_id=trace-check" in line and f"traceparent={traceparent}" in line
        for line in processed
    )


@pytest.mark.unit
def test_invalid_traceparent_is_not_propagated(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="codebreakers.worker")
    created = client.post(
        "/v1/analyses",
        json={"analyzer": "caesar-bruteforce", "text": "KHOOR"},
        headers={"traceparent": "00-zz forged=1"},
    )
    _wait_for_terminal(client, created.headers["location"])
    assert "forged" not in caplog.text
    assert "traceparent=-" in caplog.text


@pytest.mark.unit
def test_list_analyses_is_paginated(client: TestClient) -> None:
    for text in (CAESAR_CIPHERTEXT, "LXFOPVEFRNHR" * 3):
        _submit(client, {"analyzer": "caesar-bruteforce", "text": text})

    page = client.get("/v1/analyses?offset=1&limit=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["offset"] == 1
    assert page.json()["limit"] == 1
    assert len(page.json()["items"]) == 1


@pytest.mark.unit
def test_list_analyses_rejects_invalid_pagination(client: TestClient) -> None:
    response = client.get("/v1/analyses?offset=-1")
    assert response.status_code == 422
    _assert_problem(response.json(), 422, "validation-error")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("analyzer", "text", "kind"),
    [
        ("caesar-bruteforce", CAESAR_CIPHERTEXT, "ranked-candidates"),
        ("substitution-frequency", CAESAR_CIPHERTEXT, "frequency-report"),
        ("vigenere-frequency", "LXFOPVEFRNHR" * 3, "vigenere-key-analysis"),
        ("homophonic-distribution", "11 21 11 34", "frequency-report"),
    ],
)
def test_each_analyzer_returns_its_result_kind(
    client: TestClient, analyzer: str, text: str, kind: str
) -> None:
    job = _submit(client, {"analyzer": analyzer, "text": text})
    assert job["status"] == "succeeded"
    assert job["result"]["kind"] == kind
    assert job["result"]["analyzer"] == analyzer


@pytest.mark.unit
def test_non_finite_scores_serialize_as_null(client: TestClient) -> None:
    job = _submit(client, {"analyzer": "caesar-bruteforce", "text": "12345"})
    assert job["result"]["candidates"][0]["score"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"analyzer": "enigma", "text": "ABC"}, "unsupported-analyzer"),
        (
            {"analyzer": "caesar-bruteforce", "text": "ABC", "language": "klingon"},
            "unsupported-language",
        ),
    ],
)
def test_analysis_errors_return_422(
    client: TestClient, body: dict[str, str], code: str
) -> None:
    response = client.post("/v1/analyses", json=body)
    assert response.status_code == 422
    _assert_problem(response.json(), 422, code)


@pytest.mark.unit
def test_analyzer_rejection_fails_the_job_with_an_error_code(
    client: TestClient,
) -> None:
    job = _submit(client, {"analyzer": "vigenere-frequency", "text": "SHORT"})
    assert job["status"] == "failed"
    assert job["error_code"] == "insufficient-text"
    assert job["result"] is None


@pytest.mark.unit
def test_service_bus_backend_requires_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEBREAKERS_DATABASE_URL", raising=False)
    settings = ApiSettings(
        worker=WorkerSettings(queue_backend=QueueBackend.SERVICE_BUS)
    )
    with pytest.raises(ConfigurationError, match="CODEBREAKERS_DATABASE_URL"):
        create_app(settings)


@pytest.mark.unit
def test_unknown_analysis_returns_404(client: TestClient) -> None:
    response = client.get("/v1/analyses/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    _assert_problem(response.json(), 404, "analysis-not-found")


@pytest.mark.unit
def test_malformed_analysis_id_returns_422(client: TestClient) -> None:
    response = client.get("/v1/analyses/not-a-uuid")
    assert response.status_code == 422
    _assert_problem(response.json(), 422, "validation-error")


# --- Health ------------------------------------------------------------------


@pytest.mark.unit
def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.unit
def test_readiness_when_ready(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert set(response.json()["checks"].values()) == {"ok"}


@pytest.mark.unit
def test_readiness_reports_failing_checks() -> None:
    def crashing_check() -> bool:
        raise ConnectionError("database down")

    app = create_app(
        readiness_checks={
            "healthy": lambda: True,
            "unhealthy": lambda: False,
            "crashing": crashing_check,
        }
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not-ready",
        "checks": {"healthy": "ok", "unhealthy": "failing", "crashing": "failing"},
    }


# --- Log redaction -----------------------------------------------------------


@pytest.mark.unit
def test_logs_never_contain_text_or_keys(caplog: pytest.LogCaptureFixture) -> None:
    secret_text = "MEETATTHEOLDMILLATMIDNIGHT"
    secret_key = "ZEBRAS"
    app = _local_app()
    caplog.set_level(logging.DEBUG)

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            "/v1/ciphers/vigenere/encrypt",
            json={"text": secret_text, "key": secret_key},
            headers={"X-Request-ID": "redaction-check"},
        )
        client.post("/v1/ciphers/caesar/encrypt", json={"text": secret_text, "key": 1})
        client.post(
            "/v1/ciphers/caesar/encrypt", json={"text": secret_text, "key": secret_key}
        )
        _submit(client, {"analyzer": "caesar-bruteforce", "text": secret_text})
        _submit(client, {"analyzer": "vigenere-frequency", "text": secret_text})

        def exploding_runner(*_args: object) -> str:
            raise RuntimeError(secret_text)

        app.dependency_overrides[get_cipher_runner] = lambda: exploding_runner
        client.post(
            "/v1/ciphers/caesar/encrypt", json={"text": secret_text, "key": secret_key}
        )

    assert "redaction-check" in caplog.text
    assert secret_text not in caplog.text
    assert secret_key not in caplog.text
