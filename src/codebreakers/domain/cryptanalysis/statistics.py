"""Reusable text statistics for cryptanalysis."""

from collections import Counter

from codebreakers.domain.cryptanalysis.models import LanguageModel
from codebreakers.domain.models import Alphabet
from codebreakers.domain.text import normalize_text

ENGLISH_LETTER_FREQUENCIES: dict[str, float] = {
    "A": 0.0812,
    "B": 0.0149,
    "C": 0.0271,
    "D": 0.0432,
    "E": 0.1202,
    "F": 0.0230,
    "G": 0.0203,
    "H": 0.0592,
    "I": 0.0731,
    "J": 0.0010,
    "K": 0.0069,
    "L": 0.0398,
    "M": 0.0261,
    "N": 0.0695,
    "O": 0.0768,
    "P": 0.0182,
    "Q": 0.0011,
    "R": 0.0602,
    "S": 0.0628,
    "T": 0.0910,
    "U": 0.0288,
    "V": 0.0111,
    "W": 0.0209,
    "X": 0.0017,
    "Y": 0.0211,
    "Z": 0.0007,
}

ENGLISH_LANGUAGE_MODEL = LanguageModel(
    name="english",
    version="letter-frequency-v1",
    symbol_frequencies=ENGLISH_LETTER_FREQUENCIES,
)


def _supported_symbols(text: str, alphabet: Alphabet) -> str:
    normalized = normalize_text(text).upper()
    return "".join(symbol for symbol in normalized if symbol in alphabet)


def symbol_counts(text: str, alphabet: Alphabet | None = None) -> dict[str, int]:
    """Count normalized supported symbols in text."""
    active_alphabet = alphabet or Alphabet.standard_latin()
    return dict(Counter(_supported_symbols(text, active_alphabet)))


def ngram_counts(
    text: str,
    size: int,
    alphabet: Alphabet | None = None,
) -> dict[str, int]:
    """Count normalized n-grams over supported alphabet symbols."""
    if size <= 0:
        msg = "n-gram size must be greater than zero."
        raise ValueError(msg)
    active_alphabet = alphabet or Alphabet.standard_latin()
    symbols = _supported_symbols(text, active_alphabet)
    return dict(
        Counter(
            symbols[index : index + size] for index in range(len(symbols) - size + 1)
        )
    )


def index_of_coincidence(text: str, alphabet: Alphabet | None = None) -> float:
    """Calculate the index of coincidence for supported alphabet symbols."""
    counts = symbol_counts(text, alphabet)
    total = sum(counts.values())
    if total < 2:
        return 0.0
    numerator = sum(count * (count - 1) for count in counts.values())
    return numerator / (total * (total - 1))


def symbol_frequencies(text: str, alphabet: Alphabet | None = None) -> dict[str, float]:
    """Calculate normalized symbol frequencies."""
    counts = symbol_counts(text, alphabet)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {symbol: count / total for symbol, count in counts.items()}


def score_english_text(
    text: str,
    language_model: LanguageModel | None = None,
    alphabet: Alphabet | None = None,
) -> float:
    """Score text by negative chi-square distance from a language model."""
    model = language_model or ENGLISH_LANGUAGE_MODEL
    active_alphabet = alphabet or Alphabet.standard_latin()
    counts = symbol_counts(text, active_alphabet)
    total = sum(counts.values())
    if total == 0:
        return float("-inf")

    chi_square = 0.0
    for symbol in active_alphabet.symbols:
        expected_frequency = model.symbol_frequencies.get(symbol.upper(), 0.0)
        expected_count = expected_frequency * total
        if expected_count == 0.0:
            continue
        observed_count = counts.get(symbol, 0)
        chi_square += ((observed_count - expected_count) ** 2) / expected_count
    return -chi_square
