"""Homophonic substitution cipher implementation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random

from codebreakers.domain.errors import (
    AmbiguousKeyError,
    IncompleteKeyError,
    InvalidKeyError,
)
from codebreakers.domain.models import Alphabet, TransformOptions
from codebreakers.domain.text import (
    apply_case,
    handle_unknown_symbol,
    normalize_text,
    resolve_alphabet_index,
)


@dataclass(frozen=True, slots=True)
class HomophonicKey:
    """Validated one-to-many plaintext symbol to ciphertext token mapping."""

    mappings: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def from_mapping(
        cls,
        alphabet: Alphabet,
        mapping: Mapping[str, Sequence[str]],
    ) -> "HomophonicKey":
        """Create a validated homophonic key from plaintext symbol mappings."""
        normalized_items: list[tuple[str, tuple[str, ...]]] = []
        all_tokens: list[str] = []
        for source, targets in mapping.items():
            normalized_source = normalize_text(source)
            normalized_targets = tuple(normalize_text(target) for target in targets)
            if len(normalized_source) != 1 or normalized_source not in alphabet:
                raise InvalidKeyError(
                    "Homophonic key source symbols must be in alphabet."
                )
            if not normalized_targets or any(
                not token or any(character.isspace() for character in token)
                for token in normalized_targets
            ):
                raise InvalidKeyError(
                    "Homophonic key mappings must contain non-empty tokens."
                )
            normalized_items.append((normalized_source, normalized_targets))
            all_tokens.extend(normalized_targets)

        sources = tuple(source for source, _ in normalized_items)
        if set(sources) != set(alphabet.symbols):
            raise IncompleteKeyError(
                "Homophonic key must cover every alphabet symbol exactly once."
            )
        if len(set(sources)) != len(sources):
            raise AmbiguousKeyError(
                "Homophonic key cannot contain duplicate source symbols."
            )
        if len(set(all_tokens)) != len(all_tokens):
            raise AmbiguousKeyError(
                "Homophonic ciphertext tokens must be unique across mappings."
            )
        return cls(tuple(sorted(normalized_items)))

    @property
    def forward(self) -> dict[str, tuple[str, ...]]:
        """Plaintext symbol to possible ciphertext tokens."""
        return dict(self.mappings)

    @property
    def reverse(self) -> dict[str, str]:
        """Ciphertext token to plaintext symbol mapping."""
        return {token: source for source, tokens in self.mappings for token in tokens}


class HomophonicSubstitutionCipher:
    """Homophonic substitution with injected randomness for encryption."""

    def __init__(self, random_source: Random | None = None) -> None:
        self._random_source = random_source or Random()

    def encrypt(
        self,
        text: str,
        key: HomophonicKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Encrypt plaintext into space-separated ciphertext tokens."""
        opts = options or TransformOptions()
        forward = key.forward
        tokens: list[str] = []
        for symbol in normalize_text(text):
            index = resolve_alphabet_index(symbol, opts)
            if index is None:
                replacement = handle_unknown_symbol(symbol, opts)
                if replacement is not None:
                    tokens.append(replacement)
                continue
            plain_symbol = opts.alphabet.symbol_at(index)
            tokens.append(self._random_source.choice(forward[plain_symbol]))
        return " ".join(tokens)

    def decrypt(
        self,
        ciphertext: str,
        key: HomophonicKey,
        options: TransformOptions | None = None,
    ) -> str:
        """Decrypt space-separated ciphertext tokens deterministically."""
        opts = options or TransformOptions()
        reverse = key.reverse
        plaintext: list[str] = []
        for token in ciphertext.split():
            if token in reverse:
                plaintext.append(apply_case(reverse[token], token, opts))
                continue
            replacement = handle_unknown_symbol(token, opts)
            if replacement is not None:
                plaintext.append(replacement)
        return "".join(plaintext)
