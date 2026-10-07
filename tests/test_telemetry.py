"""Tests for opt-in Azure Monitor telemetry configuration."""

from unittest.mock import patch

import pytest

from codebreakers.infrastructure.telemetry import (
    CLIENT_ID_ENV,
    CONNECTION_STRING_ENV,
    LOGGER_NAME,
    configure_telemetry,
)

_CONNECTION = "InstrumentationKey=00000000-0000-0000-0000-000000000000"


@pytest.mark.unit
@pytest.mark.parametrize("env", [{}, {CONNECTION_STRING_ENV: ""}])
def test_telemetry_is_disabled_without_a_connection_string(
    env: dict[str, str],
) -> None:
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as configure:
        assert configure_telemetry(env) is False
    configure.assert_not_called()


@pytest.mark.unit
def test_telemetry_exports_only_application_loggers() -> None:
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as configure:
        assert configure_telemetry({CONNECTION_STRING_ENV: _CONNECTION}) is True
    configure.assert_called_once_with(
        connection_string=_CONNECTION,
        logger_name=LOGGER_NAME,
        enable_live_metrics=False,
    )


@pytest.mark.unit
def test_telemetry_authenticates_with_the_user_assigned_identity() -> None:
    env = {CONNECTION_STRING_ENV: _CONNECTION, CLIENT_ID_ENV: "client-id"}
    with (
        patch("azure.monitor.opentelemetry.configure_azure_monitor") as configure,
        patch("azure.identity.ManagedIdentityCredential") as credential,
    ):
        assert configure_telemetry(env) is True
    credential.assert_called_once_with(client_id="client-id")
    assert configure.call_args.kwargs["credential"] is credential.return_value


@pytest.mark.unit
def test_telemetry_reads_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONNECTION_STRING_ENV, raising=False)
    assert configure_telemetry() is False
