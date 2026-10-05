"""Typer application exposing cipher operations as a command-line tool."""

import json
import logging
import math
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING

import typer

from codebreakers.application.errors import (
    UnsupportedAnalyzerError,
    UnsupportedCipherError,
    UnsupportedLanguageError,
)
from codebreakers.application.services import CipherOperation
from codebreakers.cli.exit_codes import ExitCode
from codebreakers.composition import create_analysis_service, run_cipher
from codebreakers.domain.errors import (
    AlphabetError,
    AnalysisError,
    CipherKeyError,
    UnknownSymbolError,
)

if TYPE_CHECKING:
    from codebreakers.infrastructure.messaging.settlement import (
        DeadLetterQueue,
        DeadLetterReport,
    )
    from codebreakers.worker.settings import WorkerSettings

app = typer.Typer(
    name="codebreakers",
    help="Encrypt and decrypt text using classical ciphers.",
    add_completion=False,
)
deadletter_app = typer.Typer(
    help=(
        "Inspect, replay, or discard dead-lettered analysis job messages. "
        "Only message metadata and job IDs are shown, never job input."
    ),
    no_args_is_help=True,
)
app.add_typer(deadletter_app, name="deadletter")

_analysis_service = create_analysis_service()


def _read_text(text: str | None) -> str:
    if text is not None:
        return text
    if sys.stdin.isatty():
        typer.echo("No --text provided and no piped input detected.", err=True)
        raise typer.Exit(code=ExitCode.INVALID_USAGE)
    return sys.stdin.read().rstrip("\n")


def _fail(message: str, code: ExitCode) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=code)


def _run(
    operation: CipherOperation,
    cipher: str,
    key: str,
    text: str | None,
    alphabet: str,
) -> None:
    input_text = _read_text(text)
    try:
        result = run_cipher(cipher, operation, input_text, key, alphabet)
    except UnsupportedCipherError as err:
        raise _fail(str(err), ExitCode.UNSUPPORTED_CIPHER) from err
    except AlphabetError as err:
        raise _fail(f"Invalid alphabet: {err}", ExitCode.INVALID_ALPHABET) from err
    except CipherKeyError as err:
        raise _fail(f"Invalid key: {err}", ExitCode.INVALID_KEY) from err
    except UnknownSymbolError as err:
        raise _fail(f"Invalid input: {err}", ExitCode.INVALID_USAGE) from err

    typer.echo(result)


@app.command()
def encrypt(
    cipher: str = typer.Option(..., "--cipher", help="Name of the cipher to use."),
    key: str = typer.Option(..., "--key", help="Cipher key value."),
    text: str | None = typer.Option(
        None, "--text", help="Text to encrypt. Reads standard input if omitted."
    ),
    alphabet: str = typer.Option(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "--alphabet",
        help="Alphabet symbols used by the cipher.",
    ),
) -> None:
    """Encrypt TEXT (or standard input) using the given cipher and key.

    Example:
        codebreakers encrypt --cipher caesar --key 3 --text "HELLO"
    """
    _run(CipherOperation.ENCRYPT, cipher, key, text, alphabet)


@app.command()
def decrypt(
    cipher: str = typer.Option(..., "--cipher", help="Name of the cipher to use."),
    key: str = typer.Option(..., "--key", help="Cipher key value."),
    text: str | None = typer.Option(
        None, "--text", help="Text to decrypt. Reads standard input if omitted."
    ),
    alphabet: str = typer.Option(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "--alphabet",
        help="Alphabet symbols used by the cipher.",
    ),
) -> None:
    """Decrypt TEXT (or standard input) using the given cipher and key.

    Example:
        codebreakers decrypt --cipher caesar --key 3 --text "KHOOR"
    """
    _run(CipherOperation.DECRYPT, cipher, key, text, alphabet)


def _finite_or_none(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_or_none(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite_or_none(item) for item in value]
    return value


@app.command()
def analyze(
    analyzer: str = typer.Option(
        ...,
        "--analyzer",
        help=(
            "Analyzer to run: caesar-bruteforce, substitution-frequency, "
            "vigenere-frequency, or homophonic-distribution."
        ),
    ),
    text: str | None = typer.Option(
        None, "--text", help="Ciphertext to analyze. Reads standard input if omitted."
    ),
    language: str = typer.Option(
        "english", "--language", help="Language model used for scoring."
    ),
) -> None:
    """Analyze ciphertext without a key and print a JSON report.

    Example:
        codebreakers analyze --analyzer caesar-bruteforce --text "KHOOR ZRUOG"
    """
    input_text = _read_text(text)
    try:
        outcome = _analysis_service.analyze(analyzer, input_text, language)
    except UnsupportedAnalyzerError as err:
        raise _fail(str(err), ExitCode.UNSUPPORTED_ANALYZER) from err
    except UnsupportedLanguageError as err:
        raise _fail(str(err), ExitCode.INVALID_USAGE) from err
    except AnalysisError as err:
        raise _fail(f"Analysis failed: {err}", ExitCode.INSUFFICIENT_TEXT) from err

    # Non-finite scores become null so the output is always valid JSON.
    report = _finite_or_none(asdict(outcome))
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8000, "--port", min=1, max=65535, help="Port to bind."),
    graceful_timeout: int = typer.Option(
        10,
        "--graceful-timeout",
        min=0,
        help=(
            "Seconds to let in-flight requests finish after SIGTERM or SIGINT "
            "before closing remaining connections."
        ),
    ),
) -> None:
    """Run the HTTP API with Uvicorn.

    On SIGTERM or SIGINT the server stops accepting connections, waits up to
    --graceful-timeout seconds for in-flight requests, then runs shutdown.

    Example:
        codebreakers serve --port 8000
    """
    import uvicorn  # deferred so cipher commands do not load the server stack

    _configure_logging()
    uvicorn.run(
        "codebreakers.api:create_app",
        factory=True,
        host=host,
        port=port,
        server_header=False,
        timeout_graceful_shutdown=graceful_timeout,
    )


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )


def _worker_settings() -> "WorkerSettings":
    from codebreakers.worker.settings import ConfigurationError, WorkerSettings

    try:
        return WorkerSettings.from_env()
    except ConfigurationError as err:
        raise _fail(str(err), ExitCode.INVALID_USAGE) from err


def _run_until_stopped(
    target: "Callable[[WorkerSettings, threading.Event], None]",
) -> None:
    from codebreakers.worker.main import install_stop_signals
    from codebreakers.worker.settings import ConfigurationError

    settings = _worker_settings()
    _configure_logging()
    stop = threading.Event()
    install_stop_signals(stop)
    try:
        target(settings, stop)
    except ConfigurationError as err:
        raise _fail(str(err), ExitCode.INVALID_USAGE) from err


@app.command()
def worker() -> None:
    """Run queued analysis jobs until SIGINT or SIGTERM.

    Requires CODEBREAKERS_DATABASE_URL. Reads the PostgreSQL job queue when
    CODEBREAKERS_ANALYSIS_QUEUE=postgres, otherwise Azure Service Bus via
    CODEBREAKERS_SERVICEBUS_CONNECTION_STRING. The current job finishes
    before the process exits.

    Example:
        codebreakers worker
    """
    from codebreakers.worker.main import run_worker

    _run_until_stopped(run_worker)


@app.command()
def relay() -> None:
    """Publish committed outbox entries to the job queue until stopped.

    The queue is chosen like the worker's (PostgreSQL or Azure Service Bus).
    Several relays may run at once; rows are claimed with SKIP LOCKED.

    Example:
        codebreakers relay
    """
    from codebreakers.worker.main import run_relay

    _run_until_stopped(run_relay)


@contextmanager
def _dead_letters() -> "Iterator[DeadLetterQueue]":
    from codebreakers.worker.main import dead_letter_queue
    from codebreakers.worker.settings import ConfigurationError

    settings = _worker_settings()
    try:
        with dead_letter_queue(settings) as queue:
            yield queue
    except ConfigurationError as err:
        raise _fail(str(err), ExitCode.INVALID_USAGE) from err


def _selection(
    sequence_numbers: list[int] | None, select_all: bool
) -> frozenset[int] | None:
    if bool(sequence_numbers) == select_all:
        raise _fail(
            "Pass one or more --sequence-number options, or --all.",
            ExitCode.INVALID_USAGE,
        )
    return None if select_all else frozenset(sequence_numbers or ())


def _echo_report(action: str, report: "DeadLetterReport") -> None:
    typer.echo(f"{action} {report.settled} message(s); skipped {report.skipped}.")


_SEQUENCE_OPTION = typer.Option(
    None, "--sequence-number", "-s", help="Sequence number to select (repeatable)."
)
_ALL_OPTION = typer.Option(False, "--all", help="Select every dead letter.")
_MAX_OPTION = typer.Option(100, "--max", min=1, max=1000, help="Upper bound.")


@deadletter_app.command("list")
def deadletter_list(max_count: int = _MAX_OPTION) -> None:
    """Print dead-letter metadata as JSON lines without removing messages.

    Example:
        codebreakers deadletter list --max 20
    """
    with _dead_letters() as queue:
        for summary in queue.list(max_count):
            typer.echo(json.dumps(asdict(summary), default=str))


@deadletter_app.command("replay")
def deadletter_replay(
    sequence_numbers: list[int] | None = _SEQUENCE_OPTION,
    select_all: bool = _ALL_OPTION,
    max_count: int = _MAX_OPTION,
) -> None:
    """Republish dead letters as fresh first attempts and remove them.

    Example:
        codebreakers deadletter replay --sequence-number 42
    """
    selection = _selection(sequence_numbers, select_all)
    with _dead_letters() as queue:
        _echo_report("Replayed", queue.replay(selection, max_count))


@deadletter_app.command("discard")
def deadletter_discard(
    sequence_numbers: list[int] | None = _SEQUENCE_OPTION,
    select_all: bool = _ALL_OPTION,
    max_count: int = _MAX_OPTION,
) -> None:
    """Permanently remove dead letters.

    Example:
        codebreakers deadletter discard --all
    """
    selection = _selection(sequence_numbers, select_all)
    with _dead_letters() as queue:
        _echo_report("Discarded", queue.discard(selection, max_count))


def main() -> None:
    """Console script entry point."""
    try:
        app()
    except typer.Exit:
        raise
    except Exception as err:  # top-level boundary: never leak a traceback
        typer.echo(f"Unexpected error: {err}", err=True)
        raise typer.Exit(code=ExitCode.UNEXPECTED_ERROR) from err


if __name__ == "__main__":
    main()
