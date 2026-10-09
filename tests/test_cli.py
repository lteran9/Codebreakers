"""Tests for the Codebreakers CLI using Typer's test runner."""

import json
import re
import sys
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import codebreakers.cli.main as cli_main
from codebreakers.cli.exit_codes import ExitCode
from codebreakers.cli.main import app, main
from codebreakers.domain.errors import InvalidKeyError

runner = CliRunner()


@pytest.mark.unit
def test_encrypt_with_direct_text() -> None:
    result = runner.invoke(
        app, ["encrypt", "--cipher", "caesar", "--key", "3", "--text", "HELLO"]
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == "KHOOR"


@pytest.mark.unit
def test_decrypt_with_direct_text() -> None:
    result = runner.invoke(
        app, ["decrypt", "--cipher", "caesar", "--key", "3", "--text", "KHOOR"]
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == "HELLO"


@pytest.mark.unit
def test_encrypt_with_standard_input() -> None:
    result = runner.invoke(
        app,
        ["encrypt", "--cipher", "caesar", "--key", "3"],
        input="HELLO WORLD\n",
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == "KHOOR ZRUOG"


@pytest.mark.unit
def test_round_trip_via_pipeline() -> None:
    encrypt_result = runner.invoke(
        app, ["encrypt", "--cipher", "caesar", "--key", "5", "--text", "ATTACK AT DAWN"]
    )
    assert encrypt_result.exit_code == ExitCode.SUCCESS

    decrypt_result = runner.invoke(
        app,
        ["decrypt", "--cipher", "caesar", "--key", "5"],
        input=encrypt_result.stdout,
    )
    assert decrypt_result.exit_code == ExitCode.SUCCESS
    assert decrypt_result.stdout.strip() == "ATTACK AT DAWN"


@pytest.mark.unit
def test_custom_alphabet() -> None:
    result = runner.invoke(
        app,
        [
            "encrypt",
            "--cipher",
            "caesar",
            "--key",
            "2",
            "--text",
            "1239",
            "--alphabet",
            "0123456789",
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == "3451"


@pytest.mark.unit
def test_help_text() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == ExitCode.SUCCESS
    assert "encrypt" in result.stdout
    assert "decrypt" in result.stdout


@pytest.mark.unit
def test_encrypt_help_shows_example() -> None:
    result = runner.invoke(app, ["encrypt", "--help"])

    assert result.exit_code == ExitCode.SUCCESS

    # Typer/Rich may add ANSI escape sequences and wrap long help lines.
    plain_help = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    normalized_help = re.sub(r"\s+", " ", plain_help)

    assert "codebreakers encrypt --cipher caesar --key 3" in normalized_help


@pytest.mark.unit
def test_unsupported_cipher_exit_code() -> None:
    result = runner.invoke(
        app, ["encrypt", "--cipher", "rot47", "--key", "3", "--text", "HI"]
    )
    assert result.exit_code == ExitCode.UNSUPPORTED_CIPHER
    assert "Unsupported cipher" in result.output


@pytest.mark.unit
def test_invalid_alphabet_exit_code() -> None:
    result = runner.invoke(
        app,
        [
            "encrypt",
            "--cipher",
            "caesar",
            "--key",
            "3",
            "--text",
            "HI",
            "--alphabet",
            "AAB",
        ],
    )
    assert result.exit_code == ExitCode.INVALID_ALPHABET
    assert "Invalid alphabet" in result.output


@pytest.mark.unit
def test_invalid_key_type_exit_code() -> None:
    result = runner.invoke(
        app, ["encrypt", "--cipher", "caesar", "--key", "not-an-int", "--text", "HI"]
    )
    assert result.exit_code == ExitCode.INVALID_KEY


@pytest.mark.unit
def test_invalid_key_from_service_exit_code() -> None:
    with patch.object(
        cli_main,
        "run_cipher",
        side_effect=InvalidKeyError("key must be within supported range"),
    ):
        result = runner.invoke(
            app, ["encrypt", "--cipher", "caesar", "--key", "3", "--text", "HI"]
        )
    assert result.exit_code == ExitCode.INVALID_KEY
    assert "Invalid key: key must be within supported range" in result.output


@pytest.mark.unit
def test_empty_non_tty_stdin_is_accepted() -> None:
    result = runner.invoke(
        app, ["encrypt", "--cipher", "caesar", "--key", "3"], input=""
    )
    # Typer's CliRunner always provides a non-tty stream, so empty input
    # is read as an empty string rather than triggering the tty check.
    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout.strip() == ""


@pytest.mark.unit
def test_no_stack_trace_leaked_on_domain_error() -> None:
    result = runner.invoke(
        app,
        [
            "encrypt",
            "--cipher",
            "caesar",
            "--key",
            "3",
            "--text",
            "HI",
            "--alphabet",
            "",
        ],
    )
    assert result.exit_code == ExitCode.INVALID_ALPHABET
    assert "Traceback" not in result.output


@pytest.mark.unit
def test_no_text_and_interactive_tty_reports_invalid_usage() -> None:
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        pytest.raises(typer.Exit) as exc_info,
    ):
        cli_main._read_text(None)
    assert exc_info.value.exit_code == ExitCode.INVALID_USAGE


@pytest.mark.unit
def test_main_reports_unexpected_error_without_traceback() -> None:
    with (
        patch.object(cli_main, "app", side_effect=RuntimeError("boom")),
        pytest.raises(typer.Exit) as exc_info,
    ):
        main()
    assert exc_info.value.exit_code == ExitCode.UNEXPECTED_ERROR


CAESAR_CIPHERTEXT = (
    "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ DQG NHHSV UXQQLQJ WKURXJK WKH ILHOG"
)


@pytest.mark.unit
def test_analyze_prints_ranked_json() -> None:
    result = runner.invoke(
        app,
        ["analyze", "--analyzer", "caesar-bruteforce", "--text", CAESAR_CIPHERTEXT],
    )
    assert result.exit_code == ExitCode.SUCCESS
    report = json.loads(result.stdout)
    assert report["analyzer"] == "caesar-bruteforce"
    assert report["candidates"][0]["key"] == "3"


@pytest.mark.unit
def test_analyze_reads_standard_input() -> None:
    result = runner.invoke(
        app,
        ["analyze", "--analyzer", "homophonic-distribution"],
        input="11 21 11\n",
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert json.loads(result.stdout)["symbol_counts"] == {"11": 2, "21": 1}


@pytest.mark.unit
def test_analyze_emits_null_for_non_finite_scores() -> None:
    result = runner.invoke(
        app, ["analyze", "--analyzer", "caesar-bruteforce", "--text", "123"]
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert json.loads(result.stdout)["candidates"][0]["score"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "exit_code"),
    [
        (["--analyzer", "enigma", "--text", "ABC"], ExitCode.UNSUPPORTED_ANALYZER),
        (
            ["--analyzer", "caesar-bruteforce", "--text", "A", "--language", "xx"],
            ExitCode.INVALID_USAGE,
        ),
        (
            ["--analyzer", "vigenere-frequency", "--text", "SHORT"],
            ExitCode.INSUFFICIENT_TEXT,
        ),
    ],
)
def test_analyze_error_exit_codes(args: list[str], exit_code: ExitCode) -> None:
    result = runner.invoke(app, ["analyze", *args])
    assert result.exit_code == exit_code
    assert "Traceback" not in result.output


@pytest.mark.unit
def test_serve_runs_uvicorn_factory_on_loopback() -> None:
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "--port", "9000"])
    assert result.exit_code == ExitCode.SUCCESS
    run.assert_called_once_with(
        "codebreakers.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=9000,
        server_header=False,
        timeout_graceful_shutdown=10,
        log_config=None,
        access_log=False,
    )


@pytest.mark.unit
def test_serve_accepts_container_bind_and_graceful_timeout() -> None:
    with patch("uvicorn.run") as run:
        result = runner.invoke(
            app, ["serve", "--host", "0.0.0.0", "--graceful-timeout", "25"]
        )
    assert result.exit_code == ExitCode.SUCCESS
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["timeout_graceful_shutdown"] == 25


@pytest.mark.unit
def test_serve_configures_telemetry_before_creating_the_app() -> None:
    with patch(
        "codebreakers.infrastructure.telemetry.configure_telemetry",
        return_value=True,
    ) as configure:
        with patch(
            "uvicorn.run", side_effect=lambda *_a, **_k: configure.assert_called_once()
        ) as run:
            result = runner.invoke(app, ["serve"])
    assert result.exit_code == ExitCode.SUCCESS
    run.assert_called_once()
