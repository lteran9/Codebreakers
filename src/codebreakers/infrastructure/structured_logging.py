"""Structured JSON logs with request and job correlation.

Every record from a configured process carries ``service``, ``environment``,
``version``, ``revision``, ``event``, and, when bound, ``correlation_id`` and
``job_id``. The fields are added by a log record factory, so they reach both
stdout and the Azure Monitor log exporter.

Logs describe what happened to a request or job, never what it contained.
Submitted text can be a real historical document, so payloads are never
logged; as a safety net, values of sensitive field names are replaced, URL
credentials are masked, and exception messages, which may echo input, are
dropped in favour of the exception type and stack.

Bind correlation fields with :func:`bind_log_context` rather than ``extra=``:
the record factory sets them first, and ``logging`` rejects an ``extra`` key
that is already present on the record.
"""

import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from codebreakers import __version__

ENVIRONMENT_ENV = "CODEBREAKERS_ENVIRONMENT"
REVISION_ENV = "CODEBREAKERS_REVISION"
LOG_FORMAT_ENV = "CODEBREAKERS_LOG_FORMAT"
LOG_LEVEL_ENV = "CODEBREAKERS_LOG_LEVEL"

REDACTED = "[REDACTED]"
CONTEXT_FIELDS = ("correlation_id", "job_id")
SENSITIVE_FIELDS = frozenset(
    {
        "authorization",
        "ciphertext",
        "connection_string",
        "database_url",
        "key",
        "password",
        "plaintext",
        "secret",
        "text",
        "token",
    }
)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_EVENT = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", (), None)).keys()
) | {"message", "asctime", "service", "environment", "version", "revision", "event"}

_context: ContextVar[Mapping[str, str]] = ContextVar("codebreakers_log_context")


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    """Static fields that identify the process emitting a log record."""

    service: str
    environment: str
    version: str
    revision: str

    @classmethod
    def from_env(cls, service: str, env: Mapping[str, str] | None = None) -> Self:
        """Read the environment and revision set by deployment and image build."""
        source = os.environ if env is None else env
        return cls(
            service=service,
            environment=source.get(ENVIRONMENT_ENV) or "local",
            version=__version__,
            revision=source.get(REVISION_ENV) or "unknown",
        )


@contextmanager
def bind_log_context(**fields: str | None) -> Iterator[None]:
    """Attach correlation fields to every record logged inside the block."""
    current = dict(_context.get({}))
    current.update({k: v for k, v in fields.items() if v is not None})
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


def current_log_context() -> Mapping[str, str]:
    """Return the correlation fields bound in the current context."""
    return _context.get({})


def mask_url_credentials(value: str) -> str:
    """Replace ``user:password@`` in any URL with a redaction marker."""
    return _URL_CREDENTIALS.sub(rf"\g<scheme>{REDACTED}@", value)


def event_name(record: logging.LogRecord) -> str:
    """Use the first word of the message template as the event name."""
    head = str(record.msg).split(" ", 1)[0]
    return head if _EVENT.fullmatch(head) else "log"


def _install_record_factory(identity: ServiceIdentity) -> None:
    base = logging.getLogRecordFactory()
    # Re-configuring replaces our earlier factory rather than stacking on it.
    base = getattr(base, "__wrapped_factory__", base)

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        record.service = identity.service
        record.environment = identity.environment
        record.version = identity.version
        record.revision = identity.revision
        record.event = event_name(record)
        for name, value in current_log_context().items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return record

    factory.__wrapped_factory__ = base  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


class JsonFormatter(logging.Formatter):
    """Render one JSON object per line with redaction applied."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or event_name(record),
            "message": mask_url_credentials(record.getMessage()),
        }
        for name in ("service", "environment", "version", "revision"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        for name, value in record.__dict__.items():
            if name in _RESERVED or name.startswith("_"):
                continue
            payload[name] = _redact(name, value)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, _exc, tb = record.exc_info
            payload["exception_type"] = exc_type.__name__
            payload["stack"] = mask_url_credentials("".join(traceback.format_tb(tb)))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable lines for local development, with the same redaction."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        # Another handler may have cached the full exception text, message
        # included; format from exc_info so formatException applies.
        cached, record.exc_text = record.exc_text, None
        try:
            line = mask_url_credentials(super().format(record))
        finally:
            record.exc_text = cached
        context = " ".join(
            f"{name}={getattr(record, name)}"
            for name in CONTEXT_FIELDS
            if hasattr(record, name) and f"{name}=" not in line
        )
        return f"{line} {context}" if context else line

    def formatException(self, ei: Any) -> str:  # noqa: N802 - stdlib name
        exc_type, _exc, tb = ei
        return f"{exc_type.__name__}\n{''.join(traceback.format_tb(tb))}".rstrip()


def _redact(name: str, value: Any) -> Any:
    if name.lower() in SENSITIVE_FIELDS:
        return REDACTED
    if isinstance(value, str):
        return mask_url_credentials(value)
    return value


def configure_logging(
    service: str, env: Mapping[str, str] | None = None
) -> ServiceIdentity:
    """Send root logs to stdout as JSON (or text) tagged with ``service``.

    ``CODEBREAKERS_LOG_FORMAT=text`` selects plain lines for local use, and
    ``CODEBREAKERS_LOG_LEVEL`` overrides the ``INFO`` default.
    """
    source = os.environ if env is None else env
    identity = ServiceIdentity.from_env(service, source)
    _install_record_factory(identity)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        TextFormatter()
        if source.get(LOG_FORMAT_ENV, "json").lower() == "text"
        else JsonFormatter()
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_codebreakers", False):
            root.removeHandler(existing)
    handler._codebreakers = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(source.get(LOG_LEVEL_ENV, "INFO").upper())
    return identity
