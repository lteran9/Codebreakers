"""Monoalphabetic substitution cipher implementation."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from codebreakers.domain.errors import (
    DuplicateKeyMappingError,
    IncompleteKeyError,
    InvalidKeyError,
)
from codebreakers.domain.models import Alphabet, TransformOptions
from codebreakers.domain.text import normalize_text, transform_text


@dataclass(frozen=True, slots=True)
class SubstitutionKey:
    """Validated bijective monoalphabetic substitution key."""

    mappings: tuple[tuple[str, str], ...]

    @classmethod
    def from_cipher_alphabet(
        cls, alphabet: Alphabet, cipher_alphabet: str
    ) -> "SubstitutionKey":
        """Create a substitution key from an ordered ciphertext alphabet."""
        normalized_cipher_alphabet = normalize_text(cipher_alphabet)
        if len(normalized_cipher_alphabet) != len(alphabet):
            raise IncompleteKeyError(
                "Substitution key must contain exactly one symbol per alphabet symbol."
            )
        return cls.from_mapping(
            alphabet,
            dict(zip(alphabet.symbols, normalized_cipher_alphabet, strict=True)),
        )

    @classmethod
    def from_mapping(
        cls,
        alphabet: Alphabet,
        mapping: Mapping[str, str],
    ) -> "SubstitutionKey":
        """Create a substitution key from plaintext-to-ciphertext mapping."""
        normalized_items = tuple(
            (normalize_text(source), normalize_text(target))
            for source, target in mapping.items()
        )
        return cls.from_pairs(alphabet, normalized_items)

    @classmethod
    def from_pairs(
        cls,
        alphabet: Alphabet,
        pairs: Iterable[tuple[str, str]],
    ) -> "SubstitutionKey":
        """Create a substitution key from pairs while detecting duplicate sources."""
        normalized_pairs = tuple(
            (normalize_text(source), normalize_text(target)) for source, target in pairs
        )
        sources = tuple(source for source, _ in normalized_pairs)
        targets = tuple(target for _, target in normalized_pairs)
        expected = set(alphabet.symbols)

        if any(len(source) != 1 or source not in alphabet for source in sources):
            raise InvalidKeyError(
                "Substitution key source symbols must be in alphabet."
            )
        if any(len(target) != 1 or target not in alphabet for target in targets):
            raise InvalidKeyError(
                "Substitution key target symbols must be in alphabet."
            )
        if len(set(sources)) != len(sources):
            raise DuplicateKeyMappingError(
                "Substitution key cannot contain duplicate source symbols."
            )
        if len(set(targets)) != len(targets):
            raise DuplicateKeyMappingError(
                "Substitution key cannot contain duplicate target symbols."
            )
        if set(sources) != expected or set(targets) != expected:
            raise IncompleteKeyError(
                "Substitution key must cover every alphabet symbol exactly once."
            )
        return cls(tuple(sorted(normalized_pairs)))

    @property
    def forward(self) -> dict[str, str]:
        """Plaintext-to-ciphertext mapping."""
        return dict(self.mappings)

    @property
    def reverse(self) -> dict[str, str]:
        """Ciphertext-to-plaintext mapping."""
        return {target: source for source, target in self.mappings}


class SubstitutionCipher:
    """Monoalphabetic substitution cipher using a bijective key."""

    def encrypt(
        self,
        text: str,
        key: SubstitutionKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Encrypt plaintext using a substitution key."""
        opts = options or TransformOptions()
        forward = key.forward
        return transform_text(
            text,
            opts,
            lambda index: forward[opts.alphabet.symbol_at(index)],
        )

    def decrypt(
        self,
        ciphertext: str,
        key: SubstitutionKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Decrypt ciphertext using a substitution key."""
        opts = options or TransformOptions()
        reverse = key.reverse
        return transform_text(
            ciphertext,
            opts,
            lambda index: reverse[opts.alphabet.symbol_at(index)],
        )
