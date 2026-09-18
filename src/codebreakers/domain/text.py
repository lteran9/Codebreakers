"""Reusable text transformation helpers for alphabet-based ciphers."""

import unicodedata
from collections.abc import Callable

from codebreakers.domain.errors import UnknownSymbolError
from codebreakers.domain.models import (
    Alphabet,
    CaseStrategy,
    TransformOptions,
    UnknownSymbolStrategy,
)


def normalize_text(text: str) -> str:
    """Normalize domain text according to the project Unicode policy."""
    return unicodedata.normalize("NFC", text)


def resolve_alphabet_index(symbol: str, options: TransformOptions) -> int | None:
    """Resolve a text symbol to an alphabet index using configured case policy."""
    alphabet = options.alphabet
    match options.case_strategy:
        case CaseStrategy.PRESERVE:
            if symbol in alphabet:
                return alphabet.index_of(symbol)
            if symbol.isupper() and symbol.lower() in alphabet:
                return alphabet.index_of(symbol.lower())
            if symbol.islower() and symbol.upper() in alphabet:
                return alphabet.index_of(symbol.upper())
        case CaseStrategy.UPPERCASE:
            if symbol.upper() in alphabet:
                return alphabet.index_of(symbol.upper())
            if symbol.lower() in alphabet:
                return alphabet.index_of(symbol.lower())
        case CaseStrategy.LOWERCASE:
            if symbol.lower() in alphabet:
                return alphabet.index_of(symbol.lower())
            if symbol.upper() in alphabet:
                return alphabet.index_of(symbol.upper())
        case CaseStrategy.IGNORE:
            if symbol in alphabet:
                return alphabet.index_of(symbol)
            if symbol.upper() in alphabet:
                return alphabet.index_of(symbol.upper())
            if symbol.lower() in alphabet:
                return alphabet.index_of(symbol.lower())
    return None


def apply_case(symbol: str, original: str, options: TransformOptions) -> str:
    """Apply the configured output case policy to a transformed symbol."""
    match options.case_strategy:
        case CaseStrategy.UPPERCASE:
            return symbol.upper()
        case CaseStrategy.LOWERCASE:
            return symbol.lower()
        case CaseStrategy.PRESERVE:
            if original.isupper():
                return symbol.upper()
            if original.islower():
                return symbol.lower()
            return symbol
        case CaseStrategy.IGNORE:
            return symbol


def handle_unknown_symbol(symbol: str, options: TransformOptions) -> str | None:
    """Handle symbols outside the active alphabet according to configured policy."""
    match options.unknown_symbol_strategy:
        case UnknownSymbolStrategy.PASS_THROUGH:
            return symbol
        case UnknownSymbolStrategy.STRIP:
            return None
        case UnknownSymbolStrategy.REJECT:
            raise UnknownSymbolError(
                f"Encountered unknown symbol '{symbol}' outside alphabet."
            )


def transform_text(
    text: str,
    options: TransformOptions,
    transform_index: Callable[[int], str],
    on_transformed_symbol: Callable[[], None] | None = None,
) -> str:
    """Transform normalized text by resolving each alphabet symbol to an index."""
    result: list[str] = []
    for symbol in normalize_text(text):
        index = resolve_alphabet_index(symbol, options)
        if index is None:
            replacement = handle_unknown_symbol(symbol, options)
            if replacement is not None:
                result.append(replacement)
            continue
        transformed = transform_index(index)
        result.append(apply_case(transformed, symbol, options))
        if on_transformed_symbol is not None:
            on_transformed_symbol()
    return "".join(result)


def normalize_alphabet_symbol(symbol: str, alphabet: Alphabet) -> str:
    """Normalize one logical alphabet symbol and verify it is present."""
    normalized = normalize_text(symbol)
    if len(normalized) != 1 or normalized not in alphabet:
        msg = f"Symbol '{symbol}' is not present in the active alphabet."
        raise UnknownSymbolError(msg)
    return normalized
