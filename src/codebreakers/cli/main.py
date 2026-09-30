"""Typer application exposing cipher operations as a command-line tool."""

import json
import logging
import math
import sys
from dataclasses import asdict

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

app = typer.Typer(
    name="codebreakers",
    help="Encrypt and decrypt text using classical ciphers.",
    add_completion=False,
)

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
) -> None:
    """Run the HTTP API with Uvicorn.

    Example:
        codebreakers serve --port 8000
    """
    import uvicorn  # deferred so cipher commands do not load the server stack

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        "codebreakers.api:create_app",
        factory=True,
        host=host,
        port=port,
        server_header=False,
    )


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
