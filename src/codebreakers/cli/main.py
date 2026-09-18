"""Typer application exposing cipher operations as a command-line tool."""

import sys

import typer

from codebreakers.application.services import (
    CipherOperation,
    CipherRequest,
    CipherService,
)
from codebreakers.cli.exit_codes import ExitCode
from codebreakers.composition import CIPHER_REGISTRY, get_cipher
from codebreakers.domain.errors import (
    AlphabetError,
    CipherKeyError,
    UnknownSymbolError,
)
from codebreakers.domain.models import Alphabet, TransformOptions

app = typer.Typer(
    name="codebreakers",
    help="Encrypt and decrypt text using classical ciphers.",
    add_completion=False,
)

_service = CipherService()


def _read_text(text: str | None) -> str:
    if text is not None:
        return text
    if sys.stdin.isatty():
        typer.echo("No --text provided and no piped input detected.", err=True)
        raise typer.Exit(code=ExitCode.INVALID_USAGE)
    return sys.stdin.read().rstrip("\n")


def _run(
    operation: CipherOperation,
    cipher: str,
    key: str,
    text: str | None,
    alphabet: str,
) -> None:
    if cipher not in CIPHER_REGISTRY:
        supported = ", ".join(sorted(CIPHER_REGISTRY))
        typer.echo(
            f"Unsupported cipher '{cipher}'. Supported ciphers: {supported}.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.UNSUPPORTED_CIPHER)

    try:
        options = TransformOptions(alphabet=Alphabet(alphabet))
    except AlphabetError as err:
        typer.echo(f"Invalid alphabet: {err}", err=True)
        raise typer.Exit(code=ExitCode.INVALID_ALPHABET) from err

    input_text = _read_text(text)
    selected_cipher = get_cipher(cipher)

    try:
        parsed_key = selected_cipher.parse_key(key, options)
        result = _service.process(
            selected_cipher.cipher,
            CipherRequest(
                text=input_text,
                key=parsed_key,
                operation=operation,
                options=options,
            ),
        )
    except CipherKeyError as err:
        typer.echo(f"Invalid key: {err}", err=True)
        raise typer.Exit(code=ExitCode.INVALID_KEY) from err
    except UnknownSymbolError as err:
        typer.echo(f"Invalid input: {err}", err=True)
        raise typer.Exit(code=ExitCode.INVALID_USAGE) from err

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
