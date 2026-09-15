"""Unit tests for the Alphabet value object."""

import pytest

from codebreakers.domain.errors import (
    DuplicateSymbolError,
    EmptyAlphabetError,
    SymbolNotFoundError,
)
from codebreakers.domain.models import Alphabet


@pytest.mark.unit
def test_standard_latin_alphabet() -> None:
    alphabet = Alphabet.standard_latin()
    assert len(alphabet) == 26
    assert "A" in alphabet
    assert "Z" in alphabet
    assert "a" not in alphabet
    assert alphabet.index_of("A") == 0
    assert alphabet.index_of("Z") == 25
    assert alphabet.symbol_at(0) == "A"
    assert alphabet.symbol_at(25) == "Z"


@pytest.mark.unit
def test_alphabet_modular_indexing() -> None:
    alphabet = Alphabet("ABC")
    assert alphabet.symbol_at(0) == "A"
    assert alphabet.symbol_at(1) == "B"
    assert alphabet.symbol_at(2) == "C"
    assert alphabet.symbol_at(3) == "A"
    assert alphabet.symbol_at(-1) == "C"
    assert alphabet.symbol_at(-3) == "A"


@pytest.mark.unit
def test_empty_alphabet_raises_error() -> None:
    with pytest.raises(EmptyAlphabetError, match="cannot be empty"):
        Alphabet("")


@pytest.mark.unit
def test_duplicate_symbols_raise_error() -> None:
    with pytest.raises(DuplicateSymbolError, match="duplicate symbols"):
        Alphabet("AABC")


@pytest.mark.unit
def test_symbol_not_found_raises_error() -> None:
    alphabet = Alphabet("ABC")
    with pytest.raises(SymbolNotFoundError, match="Symbol 'Z' not found"):
        alphabet.index_of("Z")


@pytest.mark.unit
def test_unicode_normalization_in_alphabet() -> None:
    # Decomposed e + acute accent vs composed é
    decomposed = "e\u0301"
    composed = "\u00e9"
    alphabet = Alphabet(decomposed + "ABC")
    assert composed in alphabet
    assert alphabet.index_of(composed) == 0
