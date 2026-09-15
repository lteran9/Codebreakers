"""Application services coordinating domain cipher operations."""

from dataclasses import dataclass, field
from enum import Enum, auto

from codebreakers.domain.models import TransformOptions
from codebreakers.domain.protocols import Cipher


class CipherOperation(Enum):
    """Supported cipher operations."""

    ENCRYPT = auto()
    DECRYPT = auto()


@dataclass(frozen=True, slots=True)
class CipherRequest[KeyT]:
    """Request payload for performing a cipher operation."""

    text: str
    key: KeyT
    operation: CipherOperation = CipherOperation.ENCRYPT
    options: TransformOptions = field(default_factory=TransformOptions)


class CipherService:
    """Application service coordinating cipher execution over domain protocols."""

    def process[KeyT](
        self,
        cipher: Cipher[KeyT],
        request: CipherRequest[KeyT],
    ) -> str:
        """Execute a cipher operation request using the provided cipher protocol."""
        match request.operation:
            case CipherOperation.ENCRYPT:
                return cipher.encrypt(
                    text=request.text,
                    key=request.key,
                    options=request.options,
                )
            case CipherOperation.DECRYPT:
                return cipher.decrypt(
                    ciphertext=request.text,
                    key=request.key,
                    options=request.options,
                )

    def encrypt[KeyT](
        self,
        cipher: Cipher[KeyT],
        text: str,
        key: KeyT,
        options: TransformOptions | None = None,
    ) -> str:
        """Convenience method to encrypt text using the provided cipher protocol."""
        return cipher.encrypt(text=text, key=key, options=options)

    def decrypt[KeyT](
        self,
        cipher: Cipher[KeyT],
        ciphertext: str,
        key: KeyT,
        options: TransformOptions | None = None,
    ) -> str:
        """Convenience method to decrypt text using the provided cipher protocol."""
        return cipher.decrypt(ciphertext=ciphertext, key=key, options=options)
