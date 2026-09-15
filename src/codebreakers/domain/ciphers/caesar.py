"""Caesar cipher implementation."""

import unicodedata

from codebreakers.domain.errors import InvalidKeyError, UnknownSymbolError
from codebreakers.domain.models import (
    CaseStrategy,
    TransformOptions,
    UnknownSymbolStrategy,
)


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

        normalized_text = unicodedata.normalize("NFC", text)
        alphabet = options.alphabet
        effective_shift = shift % len(alphabet)
        result: list[str] = []

        for ch in normalized_text:
            transformed = self._transform_char(ch, effective_shift, options)
            if transformed is not None:
                result.append(transformed)

        return "".join(result)

    def _transform_char(
        self,
        ch: str,
        effective_shift: int,
        options: TransformOptions,
    ) -> str | None:
        alphabet = options.alphabet
        case_strat = options.case_strategy

        if case_strat == CaseStrategy.PRESERVE:
            if ch in alphabet:
                idx = alphabet.index_of(ch)
                shifted = alphabet.symbol_at(idx + effective_shift)
                return self._apply_preserved_case(
                    shifted,
                    original_is_upper=ch.isupper(),
                    original_is_lower=ch.islower(),
                )
            if ch.isupper() and ch.lower() in alphabet:
                idx = alphabet.index_of(ch.lower())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.upper()
            if ch.islower() and ch.upper() in alphabet:
                idx = alphabet.index_of(ch.upper())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.lower()

        elif case_strat == CaseStrategy.UPPERCASE:
            if ch.upper() in alphabet:
                idx = alphabet.index_of(ch.upper())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.upper()
            if ch.lower() in alphabet:
                idx = alphabet.index_of(ch.lower())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.upper()

        elif case_strat == CaseStrategy.LOWERCASE:
            if ch.lower() in alphabet:
                idx = alphabet.index_of(ch.lower())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.lower()
            if ch.upper() in alphabet:
                idx = alphabet.index_of(ch.upper())
                shifted = alphabet.symbol_at(idx + effective_shift)
                return shifted.lower()

        elif case_strat == CaseStrategy.IGNORE:
            if ch in alphabet:
                idx = alphabet.index_of(ch)
                return alphabet.symbol_at(idx + effective_shift)
            if ch.upper() in alphabet:
                idx = alphabet.index_of(ch.upper())
                return alphabet.symbol_at(idx + effective_shift)
            if ch.lower() in alphabet:
                idx = alphabet.index_of(ch.lower())
                return alphabet.symbol_at(idx + effective_shift)

        # Character is not in alphabet (even after case adjustments)
        match options.unknown_symbol_strategy:
            case UnknownSymbolStrategy.PASS_THROUGH:
                return ch
            case UnknownSymbolStrategy.STRIP:
                return None
            case UnknownSymbolStrategy.REJECT:
                raise UnknownSymbolError(
                    f"Encountered unknown symbol '{ch}' outside alphabet."
                )

    def _apply_preserved_case(
        self,
        symbol: str,
        original_is_upper: bool,
        original_is_lower: bool,
    ) -> str:
        if original_is_upper:
            return symbol.upper()
        if original_is_lower:
            return symbol.lower()
        return symbol
