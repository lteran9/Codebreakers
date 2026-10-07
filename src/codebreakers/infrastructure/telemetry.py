"""Opt-in Azure Monitor telemetry for the API, relay, and worker processes.

Nothing is exported unless ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, so
local runs, tests, and Compose never send telemetry. When ``AZURE_CLIENT_ID``
is also set, the exporter authenticates with that user-assigned managed
identity, which lets Application Insights reject key-only ingestion.
"""

import os
from collections.abc import Mapping
from typing import Any

CONNECTION_STRING_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
CLIENT_ID_ENV = "AZURE_CLIENT_ID"
# Only application loggers are exported; SDK and server loggers stay local.
LOGGER_NAME = "codebreakers"


def configure_telemetry(env: Mapping[str, str] | None = None) -> bool:
    """Export traces, metrics, and ``codebreakers`` logs when configured.

    Call before the FastAPI application is created so it is instrumented.
    Returns whether telemetry was enabled.
    """
    source = os.environ if env is None else env
    connection_string = source.get(CONNECTION_STRING_ENV)
    if not connection_string:
        return False

    # Deferred so processes without telemetry never load the SDK.
    from azure.identity import ManagedIdentityCredential
    from azure.monitor.opentelemetry import configure_azure_monitor

    options: dict[str, Any] = {
        "connection_string": connection_string,
        "logger_name": LOGGER_NAME,
        "enable_live_metrics": False,
    }
    client_id = source.get(CLIENT_ID_ENV)
    if client_id:
        options["credential"] = ManagedIdentityCredential(client_id=client_id)
    configure_azure_monitor(**options)
    return True
