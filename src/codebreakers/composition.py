"""Shared composition/wiring module for cipher instances."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from codebreakers.application.services import (
    CipherOperation,
    CipherRequest,
    CipherService,
)
from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.ciphers.homophonic import (
    HomophonicKey,
    HomophonicSubstitutionCipher,
)
from codebreakers.domain.ciphers.substitution import SubstitutionCipher, SubstitutionKey
from codebreakers.domain.ciphers.vigenere import VigenereCipher, VigenereKey
from codebreakers.domain.errors import InvalidKeyError
from codebreakers.domain.models import TransformOptions
from codebreakers.domain.protocols import Cipher


class CipherSpecProtocol(Protocol):
    """Registry entry that can execute a cipher from a raw string key."""

    name: str
    cipher: Cipher[Any]
    parse_key: Callable[[str, TransformOptions], Any]
    key_help: str

    def process(
        self,
        operation: CipherOperation,
        text: str,
        raw_key: str,
        options: TransformOptions,
    ) -> str:
        """Run a cipher operation after parsing the raw key."""
        ...


@dataclass(frozen=True, slots=True)
class CipherSpec[KeyT]:
    """Typed registry entry for a concrete cipher and key parser."""

    name: str
    cipher: Cipher[KeyT]
    parse_key: Callable[[str, TransformOptions], KeyT]
    key_help: str

    def process(
        self,
        operation: CipherOperation,
        text: str,
        raw_key: str,
        options: TransformOptions,
    ) -> str:
        """Parse the key and execute through the application service."""
        key = self.parse_key(raw_key, options)
        return CipherService().process(
            self.cipher,
            CipherRequest(text=text, key=key, operation=operation, options=options),
        )


def _parse_caesar_key(raw_key: str, _options: TransformOptions) -> int:
    try:
        return int(raw_key)
    except ValueError as err:
        raise InvalidKeyError("Caesar key must be an integer.") from err


def _parse_substitution_key(raw_key: str, options: TransformOptions) -> SubstitutionKey:
    return SubstitutionKey.from_cipher_alphabet(options.alphabet, raw_key)


def _parse_vigenere_key(raw_key: str, options: TransformOptions) -> VigenereKey:
    return VigenereKey.from_keyword(raw_key, options)


def _parse_homophonic_key(raw_key: str, options: TransformOptions) -> HomophonicKey:
    try:
        decoded = json.loads(raw_key)
    except json.JSONDecodeError as err:
        raise InvalidKeyError("Homophonic key must be a JSON object.") from err
    if not isinstance(decoded, dict):
        raise InvalidKeyError("Homophonic key must be a JSON object.")

    mapping: dict[str, Sequence[str]] = {}
    for source, tokens in decoded.items():
        if not isinstance(source, str):
            raise InvalidKeyError("Homophonic key sources must be strings.")
        if not isinstance(tokens, list) or not all(
            isinstance(token, str) for token in tokens
        ):
            raise InvalidKeyError("Homophonic key targets must be lists of strings.")
        mapping[source] = tokens
    return HomophonicKey.from_mapping(options.alphabet, mapping)


CIPHER_REGISTRY: Mapping[str, CipherSpecProtocol] = cast(
    Mapping[str, CipherSpecProtocol],
    {
        "caesar": CipherSpec(
            name="caesar",
            cipher=CaesarCipher(),
            parse_key=_parse_caesar_key,
            key_help="integer shift, for example 3",
        ),
        "substitution": CipherSpec(
            name="substitution",
            cipher=SubstitutionCipher(),
            parse_key=_parse_substitution_key,
            key_help=(
                "cipher alphabet permutation, for example QWERTYUIOPASDFGHJKLZXCVBNM"
            ),
        ),
        "vigenere": CipherSpec(
            name="vigenere",
            cipher=VigenereCipher(),
            parse_key=_parse_vigenere_key,
            key_help="alphabet keyword, for example LEMON",
        ),
        "homophonic": CipherSpec(
            name="homophonic",
            cipher=HomophonicSubstitutionCipher(),
            parse_key=_parse_homophonic_key,
            key_help='JSON mapping, for example {"A":["11"],"B":["21"]}',
        ),
    },
)


def get_cipher(name: str) -> CipherSpecProtocol:
    """Look up a registered cipher by name."""
    return CIPHER_REGISTRY[name]
