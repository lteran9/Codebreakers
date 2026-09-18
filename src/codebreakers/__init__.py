"""Codebreakers: A modular Python cryptographic and cryptanalysis package."""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "AmbiguousKeyError",
    "Alphabet",
    "AlphabetError",
    "AnalysisError",
    "CaesarCipher",
    "CaseStrategy",
    "Cipher",
    "CipherKeyError",
    "CipherOperation",
    "CipherRequest",
    "CipherService",
    "CodebreakersError",
    "DomainError",
    "DuplicateKeyMappingError",
    "DuplicateSymbolError",
    "EmptyAlphabetError",
    "HomophonicKey",
    "HomophonicSubstitutionCipher",
    "IncompleteKeyError",
    "InsufficientTextError",
    "InvalidKeyError",
    "SymbolNotFoundError",
    "SubstitutionCipher",
    "SubstitutionKey",
    "TransformOptions",
    "UnknownSymbolError",
    "UnknownSymbolStrategy",
    "VigenereCipher",
    "VigenereKey",
    "__version__",
]


def __getattr__(name: str) -> object:
    """Lazily resolve public package exports to avoid eager import cycles."""
    if name in {"CipherOperation", "CipherRequest", "CipherService"}:
        from codebreakers.application import (
            CipherOperation,
            CipherRequest,
            CipherService,
        )

        return {
            "CipherOperation": CipherOperation,
            "CipherRequest": CipherRequest,
            "CipherService": CipherService,
        }[name]

    if name in {
        "Alphabet",
        "AlphabetError",
        "AmbiguousKeyError",
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
        "InsufficientTextError",
        "InvalidKeyError",
        "SubstitutionCipher",
        "SubstitutionKey",
        "SymbolNotFoundError",
        "TransformOptions",
        "UnknownSymbolError",
        "UnknownSymbolStrategy",
        "VigenereCipher",
        "VigenereKey",
    }:
        from codebreakers.domain import (
            Alphabet,
            AlphabetError,
            AmbiguousKeyError,
            AnalysisError,
            CaesarCipher,
            CaseStrategy,
            Cipher,
            CipherKeyError,
            CodebreakersError,
            DomainError,
            DuplicateKeyMappingError,
            DuplicateSymbolError,
            EmptyAlphabetError,
            HomophonicKey,
            HomophonicSubstitutionCipher,
            IncompleteKeyError,
            InsufficientTextError,
            InvalidKeyError,
            SubstitutionCipher,
            SubstitutionKey,
            SymbolNotFoundError,
            TransformOptions,
            UnknownSymbolError,
            UnknownSymbolStrategy,
            VigenereCipher,
            VigenereKey,
        )

        return {
            "Alphabet": Alphabet,
            "AlphabetError": AlphabetError,
            "AmbiguousKeyError": AmbiguousKeyError,
            "AnalysisError": AnalysisError,
            "CaesarCipher": CaesarCipher,
            "CaseStrategy": CaseStrategy,
            "Cipher": Cipher,
            "CipherKeyError": CipherKeyError,
            "CodebreakersError": CodebreakersError,
            "DomainError": DomainError,
            "DuplicateKeyMappingError": DuplicateKeyMappingError,
            "DuplicateSymbolError": DuplicateSymbolError,
            "EmptyAlphabetError": EmptyAlphabetError,
            "HomophonicKey": HomophonicKey,
            "HomophonicSubstitutionCipher": HomophonicSubstitutionCipher,
            "IncompleteKeyError": IncompleteKeyError,
            "InsufficientTextError": InsufficientTextError,
            "InvalidKeyError": InvalidKeyError,
            "SubstitutionCipher": SubstitutionCipher,
            "SubstitutionKey": SubstitutionKey,
            "SymbolNotFoundError": SymbolNotFoundError,
            "TransformOptions": TransformOptions,
            "UnknownSymbolError": UnknownSymbolError,
            "UnknownSymbolStrategy": UnknownSymbolStrategy,
            "VigenereCipher": VigenereCipher,
            "VigenereKey": VigenereKey,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
