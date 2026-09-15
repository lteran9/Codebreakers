"""Domain-level error definitions for Codebreakers."""


class CodebreakersError(Exception):
    """Base exception for all Codebreakers errors."""


class DomainError(CodebreakersError):
    """Base exception for all domain-level errors."""


class AlphabetError(DomainError):
    """Base exception for errors relating to alphabets."""


class EmptyAlphabetError(AlphabetError):
    """Raised when an alphabet is initialized with an empty sequence."""


class DuplicateSymbolError(AlphabetError):
    """Raised when an alphabet contains duplicate characters or symbols."""


class SymbolNotFoundError(AlphabetError):
    """Raised when a symbol is not present in an alphabet."""


class CipherKeyError(DomainError):
    """Base exception for errors relating to cipher keys."""


class InvalidKeyError(CipherKeyError):
    """Raised when a cipher key is malformed or invalid for the target cipher."""


class UnknownSymbolError(DomainError):
    """Raised when a text contains symbols outside the alphabet under strict policy."""
