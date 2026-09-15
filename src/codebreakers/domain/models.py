"""Domain models and value objects for cryptographic operations."""

import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto

from codebreakers.domain.errors import (
    DuplicateSymbolError,
    EmptyAlphabetError,
    SymbolNotFoundError,
)


class CaseStrategy(Enum):
    """Strategy for handling character case during transformation."""

    PRESERVE = auto()
    UPPERCASE = auto()
    LOWERCASE = auto()
    IGNORE = auto()


class UnknownSymbolStrategy(Enum):
    """Strategy for handling characters outside the cipher alphabet."""

    PASS_THROUGH = auto()
    STRIP = auto()
    REJECT = auto()


@dataclass(frozen=True, slots=True)
class Alphabet:
    """Immutable, validated sequence of unique symbols defining a cipher alphabet."""

    symbols: str

    def __post_init__(self) -> None:
        normalized = unicodedata.normalize("NFC", self.symbols)
        if not normalized:
            raise EmptyAlphabetError("Alphabet symbols cannot be empty.")
        if len(set(normalized)) != len(normalized):
            raise DuplicateSymbolError("Alphabet cannot contain duplicate symbols.")
        object.__setattr__(self, "symbols", normalized)

    def __len__(self) -> int:
        return len(self.symbols)

    def __contains__(self, symbol: str) -> bool:
        normalized_symbol = unicodedata.normalize("NFC", symbol)
        return len(normalized_symbol) == 1 and normalized_symbol in self.symbols

    def index_of(self, symbol: str) -> int:
        """Return the 0-based index of the given symbol in the alphabet."""
        normalized_symbol = unicodedata.normalize("NFC", symbol)
        try:
            return self.symbols.index(normalized_symbol)
        except ValueError as err:
            raise SymbolNotFoundError(
                f"Symbol '{symbol}' not found in alphabet."
            ) from err

    def symbol_at(self, index: int) -> str:
        """Return the symbol at the given modular index."""
        return self.symbols[index % len(self.symbols)]

    @classmethod
    def standard_latin(cls) -> "Alphabet":
        """Standard 26-letter uppercase Latin alphabet (A-Z)."""
        return cls("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@dataclass(frozen=True, slots=True)
class TransformOptions:
    """Immutable options controlling cipher text transformation."""

    alphabet: Alphabet = field(default_factory=Alphabet.standard_latin)
    case_strategy: CaseStrategy = CaseStrategy.PRESERVE
    unknown_symbol_strategy: UnknownSymbolStrategy = UnknownSymbolStrategy.PASS_THROUGH
