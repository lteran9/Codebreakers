"""Structured log format, correlation context, and redaction."""

import json
import logging
from typing import Any

import pytest

from codebreakers import __version__
from codebreakers.infrastructure.structured_logging import (
    REDACTED,
    ServiceIdentity,
    bind_log_context,
    configure_logging,
    current_log_context,
    event_name,
    mask_url_credentials,
)

SECRET = "ATTACKATDAWN"
ENV = {"CODEBREAKERS_ENVIRONMENT": "dev", "CODEBREAKERS_REVISION": "abc123"}


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


@pytest.mark.unit
def test_identity_defaults_to_local_and_unknown_revision() -> None:
    assert ServiceIdentity.from_env("api", {}) == ServiceIdentity(
        "api", "local", __version__, "unknown"
    )
    assert ServiceIdentity.from_env("worker", ENV).revision == "abc123"


@pytest.mark.unit
def test_json_lines_carry_identity_event_and_correlation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("worker", ENV)
    logger = logging.getLogger("codebreakers.test")

    with bind_log_context(correlation_id="req-1", job_id="job-1"):
        logger.info("job_processed outcome=%s", "succeeded")
    logger.info("outside_context")

    inside, outside = _lines(capsys)
    assert inside["timestamp"].endswith("+00:00")
    assert {k: inside[k] for k in ("level", "logger", "event", "message")} == {
        "level": "INFO",
        "logger": "codebreakers.test",
        "event": "job_processed",
        "message": "job_processed outcome=succeeded",
    }
    assert {
        k: inside[k] for k in ("service", "environment", "version", "revision")
    } == {
        "service": "worker",
        "environment": "dev",
        "version": __version__,
        "revision": "abc123",
    }
    assert (inside["correlation_id"], inside["job_id"]) == ("req-1", "job-1")
    assert "correlation_id" not in outside


@pytest.mark.unit
def test_nested_contexts_merge_and_unwind() -> None:
    with bind_log_context(correlation_id="outer"):
        with bind_log_context(job_id="job", correlation_id=None):
            assert current_log_context() == {"correlation_id": "outer", "job_id": "job"}
        assert current_log_context() == {"correlation_id": "outer"}
    assert current_log_context() == {}


@pytest.mark.unit
def test_sensitive_fields_urls_and_exception_messages_are_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("api", ENV)
    logger = logging.getLogger("codebreakers.test")

    logger.info(
        "connecting url=%s",
        f"postgresql://app:{SECRET}@db/codebreakers",
        extra={"plaintext": SECRET, "Key": SECRET, "dsn": f"amqp://u:{SECRET}@h"},
    )
    try:
        raise ValueError(f"cannot decrypt {SECRET}")
    except ValueError:
        logger.exception("job_failed")

    output = capsys.readouterr().out
    assert SECRET not in output
    connecting, failed = (json.loads(line) for line in output.splitlines())
    assert (
        connecting["message"]
        == f"connecting url=postgresql://{REDACTED}@db/codebreakers"
    )
    assert connecting["plaintext"] == connecting["Key"] == REDACTED
    assert connecting["dsn"] == f"amqp://{REDACTED}@h"
    assert failed["exception_type"] == "ValueError"
    assert "test_structured_logging.py" in failed["stack"]


@pytest.mark.unit
def test_text_format_appends_context_and_drops_exception_messages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        "relay", {"CODEBREAKERS_LOG_FORMAT": "text", "CODEBREAKERS_LOG_LEVEL": "debug"}
    )
    logger = logging.getLogger("codebreakers.test")

    with bind_log_context(correlation_id="req-9"):
        logger.debug("relay_tick")
        logger.info("already correlation_id=req-9")
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            logger.exception("relay_failed")

    output = capsys.readouterr().out
    assert logging.getLogger().level == logging.DEBUG
    assert "relay_tick correlation_id=req-9" in output
    assert "correlation_id=req-9 correlation_id=req-9" not in output
    assert "RuntimeError" in output
    assert SECRET not in output


@pytest.mark.unit
def test_reconfiguring_replaces_the_handler_and_record_factory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("api", ENV)
    configure_logging("worker", ENV)

    logging.getLogger("codebreakers.test").info("once")

    (line,) = _lines(capsys)
    assert line["service"] == "worker"
    marked = [h for h in logging.getLogger().handlers if hasattr(h, "_codebreakers")]
    assert len(marked) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("request_completed method=GET", "request_completed"),
        ("Started server process [1]", "log"),
        ("", "log"),
    ],
)
def test_event_name_is_the_leading_snake_case_word(message: str, expected: str) -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, message, (), None)
    assert event_name(record) == expected


@pytest.mark.unit
def test_url_credentials_are_masked_but_plain_urls_are_kept() -> None:
    assert mask_url_credentials("https://example.com/a") == "https://example.com/a"
    assert (
        mask_url_credentials("x postgresql+psycopg://u:p@h:5432/d y")
        == f"x postgresql+psycopg://{REDACTED}@h:5432/d y"
    )
