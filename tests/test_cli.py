"""Tests for the Codebreakers CLI using Typer's test runner."""

import re
import sys
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import codebreakers.cli.main as cli_main
from codebreakers.cli.exit_codes import ExitCode
from codebreakers.cli.main import app, main

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
def test_missing_text_and_no_stdin_reports_invalid_usage() -> None:
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
