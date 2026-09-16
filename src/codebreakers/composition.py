"""Shared composition/wiring module for cipher instances.

This module is the single source of truth for which ciphers exist and how
they are constructed. Both the CLI and the future HTTP API import from here
so cipher availability never diverges between interfaces.
"""

from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.protocols import Cipher

CIPHER_REGISTRY: dict[str, Cipher[int]] = {
    "caesar": CaesarCipher(),
}


def get_cipher(name: str) -> Cipher[int]:
    """Look up a registered cipher by name.

    Raises:
        KeyError: If no cipher is registered under the given name.
    """
    return CIPHER_REGISTRY[name]
