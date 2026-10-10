"""Telemetry never carries submitted content, keys, credentials, or results.

Submitted text may be a real document someone is studying, so request spans,
database spans, and logs must describe requests without containing them.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from codebreakers.api import ApiSettings, create_app
from codebreakers.infrastructure.structured_logging import configure_logging
from codebreakers.worker.execution import InlineAnalysisExecutor
from codebreakers.worker.settings import WorkerSettings

PLAINTEXT = "MEET THE COURIER AT THE OLD MILL BEFORE DAWN"
KEY = "LANTERNKEY"
TOKEN = "Bearer eyJsentinel.token.value"
COOKIE = "session=cookie-sentinel-value"


@pytest.mark.unit
def test_request_spans_and_logs_exclude_content_keys_and_credentials(
    span_exporter: InMemorySpanExporter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("api", {})
    app = create_app(
        ApiSettings(worker=WorkerSettings()), executor=InlineAnalysisExecutor()
    )
    FastAPIInstrumentor.instrument_app(app)
    headers = {"Authorization": TOKEN, "Cookie": COOKIE, "X-Request-ID": "trace-me"}
    try:
        with TestClient(app) as client:
            encrypted = client.post(
                "/v1/ciphers/vigenere/encrypt",
                json={"text": PLAINTEXT, "key": KEY},
                headers=headers,
            ).json()["result"]
            client.post(
                "/v1/ciphers/vigenere/decrypt",
                json={"text": encrypted, "key": KEY},
                headers=headers,
            )
            client.post(
                "/v1/ciphers/vigenere/encrypt",
                json={"text": PLAINTEXT, "key": "not a valid key 123"},
                headers=headers,
            )
            shifted = client.post(
                "/v1/ciphers/caesar/encrypt",
                json={"text": PLAINTEXT, "key": "7"},
                headers=headers,
            ).json()["result"]
            location = client.post(
                "/v1/analyses",
                json={
                    "analyzer": "caesar-bruteforce",
                    "text": shifted,
                    "language": "english",
                },
                headers=headers,
            ).headers["location"]
            deadline = time.monotonic() + 10
            while client.get(location).json()["status"] == "pending":
                assert time.monotonic() < deadline
                time.sleep(0.01)
            result = client.get(location, headers=headers).text
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    spans = span_exporter.get_finished_spans()
    log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    exported = "\n".join(span.to_json() for span in spans) + json.dumps(log_lines)
    assert any(span.name == "POST /v1/analyses" for span in spans)
    assert "trace-me" in exported
    assert '"event": "request_completed"' in exported
    assert any(
        line["event"] == "request_completed" and line["correlation_id"] == "trace-me"
        for line in log_lines
    )
    assert '"event": "analysis_submitted"' in exported
    assert any(
        line["event"] == "analysis_submitted"
        and line["job_id"] == location.rsplit("/", 1)[-1]
        for line in log_lines
    )
    assert '"event": "job_processed"' in exported
    for secret in (PLAINTEXT, encrypted, shifted, KEY, TOKEN, "eyJsentinel", COOKIE):
        assert secret not in exported
    # Analysis results contain candidate plaintexts; none may leak either.
    assert PLAINTEXT in result
    assert "COURIER" not in exported
