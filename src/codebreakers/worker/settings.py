"""Environment-based settings shared by the API, relay, and worker processes."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

from codebreakers.application.processing import ExecutionBudget, RetryPolicy

_MEBIBYTE = 1024 * 1024


class QueueBackend(StrEnum):
    """Where queued analysis jobs are delivered."""

    IN_PROCESS = "in-process"
    SERVICE_BUS = "service-bus"
    POSTGRES = "postgres"

    @property
    def distributed(self) -> bool:
        """Whether separate relay and worker processes deliver the jobs."""
        return self is not QueueBackend.IN_PROCESS


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Queue, budget, and retry configuration. Secrets are excluded from repr."""

    queue_backend: QueueBackend = QueueBackend.IN_PROCESS
    database_url: str | None = field(default=None, repr=False)
    servicebus_connection_string: str | None = field(default=None, repr=False)
    queue_name: str = "analysis-jobs"
    time_budget: timedelta = timedelta(seconds=30)
    memory_limit_mb: int | None = 1024
    max_attempts: int = 5
    retry_base_delay: timedelta = timedelta(seconds=2)
    retry_max_delay: timedelta = timedelta(seconds=60)
    retention_days: int = 7
    relay_poll_interval: float = 1.0
    relay_batch_size: int = 100
    heartbeat_file: Path | None = None

    def __post_init__(self) -> None:
        if self.retention_days < 1:
            msg = "CODEBREAKERS_ANALYSIS_RETENTION_DAYS must be at least 1."
            raise ConfigurationError(msg)
        try:
            self.budget()
            self.retry_policy()
        except ValueError as err:
            raise ConfigurationError(f"Invalid worker configuration: {err}") from err

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Read ``CODEBREAKERS_*`` variables, falling back to defaults."""
        source = os.environ if env is None else env
        heartbeat = source.get("CODEBREAKERS_HEARTBEAT_FILE")
        try:
            memory = int(source.get("CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB", "1024"))
            return cls(
                queue_backend=QueueBackend(
                    source.get("CODEBREAKERS_ANALYSIS_QUEUE", QueueBackend.IN_PROCESS)
                ),
                database_url=source.get("CODEBREAKERS_DATABASE_URL"),
                servicebus_connection_string=source.get(
                    "CODEBREAKERS_SERVICEBUS_CONNECTION_STRING"
                ),
                queue_name=source.get("CODEBREAKERS_SERVICEBUS_QUEUE", "analysis-jobs"),
                time_budget=timedelta(
                    seconds=float(
                        source.get("CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS", "30")
                    )
                ),
                memory_limit_mb=memory if memory != 0 else None,
                max_attempts=int(source.get("CODEBREAKERS_WORKER_MAX_ATTEMPTS", "5")),
                retry_base_delay=timedelta(
                    seconds=float(
                        source.get("CODEBREAKERS_WORKER_RETRY_BASE_SECONDS", "2")
                    )
                ),
                retry_max_delay=timedelta(
                    seconds=float(
                        source.get("CODEBREAKERS_WORKER_RETRY_MAX_SECONDS", "60")
                    )
                ),
                retention_days=int(
                    source.get("CODEBREAKERS_ANALYSIS_RETENTION_DAYS", "7")
                ),
                heartbeat_file=Path(heartbeat) if heartbeat else None,
            )
        except ValueError as err:
            raise ConfigurationError(f"Invalid worker configuration: {err}") from err

    def budget(self) -> ExecutionBudget:
        """Return the per-job execution budget."""
        return ExecutionBudget(
            time_limit=self.time_budget,
            memory_limit_bytes=(
                None
                if self.memory_limit_mb is None
                else self.memory_limit_mb * _MEBIBYTE
            ),
        )

    def retry_policy(self) -> RetryPolicy:
        """Return the bounded retry policy."""
        return RetryPolicy(
            max_attempts=self.max_attempts,
            base_delay=self.retry_base_delay,
            max_delay=self.retry_max_delay,
        )

    def require_database_url(self) -> str:
        """Return the database URL or fail with a message naming the variable."""
        if not self.database_url:
            msg = "CODEBREAKERS_DATABASE_URL must be configured."
            raise ConfigurationError(msg)
        return self.database_url

    def require_servicebus(self) -> str:
        """Return the Service Bus connection string or fail without echoing it."""
        if not self.servicebus_connection_string:
            msg = "CODEBREAKERS_SERVICEBUS_CONNECTION_STRING must be configured."
            raise ConfigurationError(msg)
        return self.servicebus_connection_string
