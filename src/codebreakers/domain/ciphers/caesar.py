"""Caesar cipher implementation."""

from codebreakers.domain.errors import InvalidKeyError
from codebreakers.domain.models import TransformOptions
from codebreakers.domain.text import transform_text


class CaesarCipher:
    """Caesar shift cipher supporting arbitrary keys and options."""

    def encrypt(
        self,
        text: str,
        key: int,
        options: TransformOptions | None = None,
    ) -> str:
        """Encrypt plaintext using a Caesar shift."""
        opts = options or TransformOptions()
        return self._transform(text, shift=key, options=opts)

    def decrypt(
        self,
        ciphertext: str,
        key: int,
        options: TransformOptions | None = None,
    ) -> str:
        """Decrypt ciphertext by inverting the Caesar shift."""
        opts = options or TransformOptions()
        if not isinstance(key, int) or isinstance(key, bool):
            raise InvalidKeyError(
                f"Caesar cipher key must be an integer, got {type(key).__name__}."
            )
        return self._transform(ciphertext, shift=-key, options=opts)

    def _transform(self, text: str, shift: int, options: TransformOptions) -> str:
        if not isinstance(shift, int) or isinstance(shift, bool):
            raise InvalidKeyError(
                f"Caesar cipher key must be an integer, got {type(shift).__name__}."
            )

        alphabet = options.alphabet
        effective_shift = shift % len(alphabet)
        return transform_text(
            text,
            options,
            lambda index: alphabet.symbol_at(index + effective_shift),
        )
