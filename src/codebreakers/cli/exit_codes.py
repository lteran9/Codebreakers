"""Exit codes for the Codebreakers CLI."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Documented process exit codes returned by the CLI."""

    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    INVALID_USAGE = 2
    INVALID_KEY = 3
    INVALID_ALPHABET = 4
    UNSUPPORTED_CIPHER = 5
