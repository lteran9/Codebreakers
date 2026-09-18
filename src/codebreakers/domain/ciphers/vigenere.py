"""Vigenere cipher implementation."""

from dataclasses import dataclass

from codebreakers.domain.errors import InvalidKeyError
from codebreakers.domain.models import TransformOptions
from codebreakers.domain.text import (
    normalize_text,
    resolve_alphabet_index,
    transform_text,
)


@dataclass(frozen=True, slots=True)
class VigenereKey:
    """Validated repeating Vigenere keyword represented as alphabet shifts."""

    keyword: str
    shifts: tuple[int, ...]

    @classmethod
    def from_keyword(
        cls,
        keyword: str,
        options: TransformOptions | None = None,
    ) -> "VigenereKey":
        """Create a Vigenere key from a non-empty alphabet keyword."""
        opts = options or TransformOptions()
        normalized_keyword = normalize_text(keyword)
        if not normalized_keyword:
            raise InvalidKeyError("Vigenere keyword cannot be empty.")

        shifts: list[int] = []
        for symbol in normalized_keyword:
            shift = resolve_alphabet_index(symbol, opts)
            if shift is None:
                raise InvalidKeyError(
                    "Vigenere keyword must contain only alphabet symbols."
                )
            shifts.append(shift)
        return cls(normalized_keyword, tuple(shifts))


class VigenereCipher:
    """Vigenere cipher with keyword advancement over alphabet symbols only."""

    def encrypt(
        self,
        text: str,
        key: VigenereKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Encrypt plaintext using a repeating keyword."""
        return self._transform(text, key, decrypt=False, options=options)

    def decrypt(
        self,
        ciphertext: str,
        key: VigenereKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Decrypt ciphertext using a repeating keyword."""
        return self._transform(ciphertext, key, decrypt=True, options=options)

    def _transform(
        self,
        text: str,
        key: VigenereKey,
        decrypt: bool,
        options: TransformOptions | None,
    ) -> str:
        opts = options or TransformOptions()
        alphabet = opts.alphabet
        position = 0

        def transform_index(index: int) -> str:
            nonlocal position
            shift = key.shifts[position % len(key.shifts)]
            if decrypt:
                shift = -shift
            return alphabet.symbol_at(index + shift)

        def advance_key() -> None:
            nonlocal position
            position += 1

        return transform_text(text, opts, transform_index, advance_key)
