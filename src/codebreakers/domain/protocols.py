"""Domain protocols for cipher algorithms."""

from typing import Protocol, TypeVar

from codebreakers.domain.models import TransformOptions

KeyT_contra = TypeVar("KeyT_contra", contravariant=True)


class Cipher(Protocol[KeyT_contra]):
    """Generic protocol for ciphers with typed keys and stateless operations."""

    def encrypt(
        self,
        text: str,
        key: KeyT_contra,
        options: TransformOptions | None = None,
    ) -> str:
        """Encrypt plaintext using the provided key and transform options."""
        ...

    def decrypt(
        self,
        ciphertext: str,
        key: KeyT_contra,
        options: TransformOptions | None = None,
    ) -> str:
        """Decrypt ciphertext using the provided key and transform options."""
        ...
