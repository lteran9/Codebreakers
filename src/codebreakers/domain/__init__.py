"""Domain layer for Codebreakers cryptographic operations."""

from codebreakers.domain.ciphers import (
    CaesarCipher,
    HomophonicKey,
    HomophonicSubstitutionCipher,
    SubstitutionCipher,
    SubstitutionKey,
    VigenereCipher,
    VigenereKey,
)
from codebreakers.domain.errors import (
    AlphabetError,
    AmbiguousKeyError,
    AnalysisError,
    CipherKeyError,
    CodebreakersError,
    DomainError,
    DuplicateKeyMappingError,
    DuplicateSymbolError,
    EmptyAlphabetError,
    IncompleteKeyError,
    InsufficientTextError,
    InvalidKeyError,
    SymbolNotFoundError,
    UnknownSymbolError,
)
from codebreakers.domain.models import (
    Alphabet,
    CaseStrategy,
    TransformOptions,
    UnknownSymbolStrategy,
)
from codebreakers.domain.protocols import Cipher

__all__ = [
    "AmbiguousKeyError",
    "Alphabet",
    "AlphabetError",
    "AnalysisError",
    "CaesarCipher",
    "CaseStrategy",
    "Cipher",
    "CipherKeyError",
    "CodebreakersError",
    "DomainError",
    "DuplicateKeyMappingError",
    "DuplicateSymbolError",
    "EmptyAlphabetError",
    "HomophonicKey",
    "HomophonicSubstitutionCipher",
    "IncompleteKeyError",
    "InvalidKeyError",
    "InsufficientTextError",
    "SymbolNotFoundError",
    "SubstitutionCipher",
    "SubstitutionKey",
    "TransformOptions",
    "UnknownSymbolError",
    "UnknownSymbolStrategy",
    "VigenereCipher",
    "VigenereKey",
]
