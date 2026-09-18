"""Domain layer for Codebreakers cryptographic operations."""

from __future__ import annotations

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


def __getattr__(name: str) -> object:
    """Lazily expose domain-layer API without eager imports."""
    if name in {
        "Alphabet",
        "CaseStrategy",
        "TransformOptions",
        "UnknownSymbolStrategy",
    }:
        from codebreakers.domain.models import (
            Alphabet,
            CaseStrategy,
            TransformOptions,
            UnknownSymbolStrategy,
        )

        return {
            "Alphabet": Alphabet,
            "CaseStrategy": CaseStrategy,
            "TransformOptions": TransformOptions,
            "UnknownSymbolStrategy": UnknownSymbolStrategy,
        }[name]

    if name in {
        "AlphabetError",
        "AmbiguousKeyError",
        "AnalysisError",
        "CipherKeyError",
        "CodebreakersError",
        "DomainError",
        "DuplicateKeyMappingError",
        "DuplicateSymbolError",
        "EmptyAlphabetError",
        "IncompleteKeyError",
        "InsufficientTextError",
        "InvalidKeyError",
        "SymbolNotFoundError",
        "UnknownSymbolError",
    }:
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

        return {
            "AlphabetError": AlphabetError,
            "AmbiguousKeyError": AmbiguousKeyError,
            "AnalysisError": AnalysisError,
            "CipherKeyError": CipherKeyError,
            "CodebreakersError": CodebreakersError,
            "DomainError": DomainError,
            "DuplicateKeyMappingError": DuplicateKeyMappingError,
            "DuplicateSymbolError": DuplicateSymbolError,
            "EmptyAlphabetError": EmptyAlphabetError,
            "IncompleteKeyError": IncompleteKeyError,
            "InsufficientTextError": InsufficientTextError,
            "InvalidKeyError": InvalidKeyError,
            "SymbolNotFoundError": SymbolNotFoundError,
            "UnknownSymbolError": UnknownSymbolError,
        }[name]

    if name in {"Cipher"}:
        from codebreakers.domain.protocols import Cipher

        return Cipher

    if name in {
        "CaesarCipher",
        "HomophonicKey",
        "HomophonicSubstitutionCipher",
        "SubstitutionCipher",
        "SubstitutionKey",
        "VigenereCipher",
        "VigenereKey",
    }:
        from codebreakers.domain.ciphers import (
            CaesarCipher,
            HomophonicKey,
            HomophonicSubstitutionCipher,
            SubstitutionCipher,
            SubstitutionKey,
            VigenereCipher,
            VigenereKey,
        )

        return {
            "CaesarCipher": CaesarCipher,
            "HomophonicKey": HomophonicKey,
            "HomophonicSubstitutionCipher": HomophonicSubstitutionCipher,
            "SubstitutionCipher": SubstitutionCipher,
            "SubstitutionKey": SubstitutionKey,
            "VigenereCipher": VigenereCipher,
            "VigenereKey": VigenereKey,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
