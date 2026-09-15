"""Unit and invariant tests for CaesarCipher."""

from typing import cast

import pytest

from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.errors import InvalidKeyError, UnknownSymbolError
from codebreakers.domain.models import (
    Alphabet,
    CaseStrategy,
    TransformOptions,
    UnknownSymbolStrategy,
)


@pytest.mark.unit
def test_caesar_encrypt_standard_example() -> None:
    cipher = CaesarCipher()
    plaintext = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG"
    expected = "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"
    assert cipher.encrypt(plaintext, key=3) == expected
    assert cipher.decrypt(expected, key=3) == plaintext


@pytest.mark.unit
def test_caesar_rot13() -> None:
    cipher = CaesarCipher()
    plaintext = "Hello, World!"
    ciphertext = cipher.encrypt(plaintext, key=13)
    assert ciphertext == "Uryyb, Jbeyq!"
    assert cipher.decrypt(ciphertext, key=13) == plaintext


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shift", "expected"),
    [
        (0, "HelloWorld"),
        (26, "HelloWorld"),
        (52, "HelloWorld"),
        (-26, "HelloWorld"),
        (3, "KhoorZruog"),
        (29, "KhoorZruog"),
        (-3, "EbiilTloia"),
        (-29, "EbiilTloia"),
        (55, "KhoorZruog"),
    ],
)
def test_caesar_shift_boundaries(shift: int, expected: str) -> None:
    cipher = CaesarCipher()
    plaintext = "HelloWorld"
    assert cipher.encrypt(plaintext, key=shift) == expected
    assert cipher.decrypt(expected, key=shift) == plaintext


@pytest.mark.unit
def test_caesar_empty_text() -> None:
    cipher = CaesarCipher()
    assert cipher.encrypt("", key=3) == ""
    assert cipher.decrypt("", key=3) == ""


@pytest.mark.unit
def test_caesar_invalid_key_type() -> None:
    cipher = CaesarCipher()
    with pytest.raises(InvalidKeyError, match="must be an integer"):
        cipher.encrypt("Hello", key=cast(int, "3"))
    with pytest.raises(InvalidKeyError, match="must be an integer"):
        cipher.encrypt("Hello", key=cast(int, True))
    with pytest.raises(InvalidKeyError, match="must be an integer"):
        cipher.encrypt("Hello", key=cast(int, 3.14))


@pytest.mark.unit
def test_caesar_case_strategy_uppercase() -> None:
    cipher = CaesarCipher()
    options = TransformOptions(case_strategy=CaseStrategy.UPPERCASE)
    assert cipher.encrypt("Hello, World!", key=3, options=options) == "KHOOR, ZRUOG!"


@pytest.mark.unit
def test_caesar_case_strategy_lowercase() -> None:
    cipher = CaesarCipher()
    options = TransformOptions(case_strategy=CaseStrategy.LOWERCASE)
    assert cipher.encrypt("Hello, World!", key=3, options=options) == "khoor, zruog!"


@pytest.mark.unit
def test_caesar_lowercase_alphabet_with_case_strategies() -> None:
    cipher = CaesarCipher()
    lower_alphabet = Alphabet("abcdefghijklmnopqrstuvwxyz")

    # PRESERVE with lowercase alphabet
    opts_preserve = TransformOptions(
        alphabet=lower_alphabet, case_strategy=CaseStrategy.PRESERVE
    )
    enc = cipher.encrypt("Hello, World!", key=3, options=opts_preserve)
    assert enc == "Khoor, Zruog!"
    dec = cipher.decrypt("Khoor, Zruog!", key=3, options=opts_preserve)
    assert dec == "Hello, World!"

    # UPPERCASE with lowercase alphabet
    opts_upper = TransformOptions(
        alphabet=lower_alphabet, case_strategy=CaseStrategy.UPPERCASE
    )
    assert cipher.encrypt("hello", key=3, options=opts_upper) == "KHOOR"

    # LOWERCASE with lowercase alphabet
    opts_lower = TransformOptions(
        alphabet=lower_alphabet, case_strategy=CaseStrategy.LOWERCASE
    )
    assert cipher.encrypt("HELLO", key=3, options=opts_lower) == "khoor"
    assert cipher.encrypt("hello", key=3, options=opts_lower) == "khoor"

    # IGNORE with lowercase alphabet
    opts_ignore = TransformOptions(
        alphabet=lower_alphabet, case_strategy=CaseStrategy.IGNORE
    )
    assert cipher.encrypt("HELLO", key=3, options=opts_ignore) == "khoor"
    assert cipher.encrypt("hello", key=3, options=opts_ignore) == "khoor"


@pytest.mark.unit
def test_caesar_non_cased_symbols_in_alphabet() -> None:
    cipher = CaesarCipher()
    symbol_alphabet = Alphabet("!@#$%")
    opts = TransformOptions(
        alphabet=symbol_alphabet, case_strategy=CaseStrategy.PRESERVE
    )
    assert cipher.encrypt("!@", key=1, options=opts) == "@#"
    assert cipher.decrypt("@#", key=1, options=opts) == "!@"


@pytest.mark.unit
def test_caesar_ignore_casing_strategy() -> None:
    cipher = CaesarCipher()
    opts = TransformOptions(case_strategy=CaseStrategy.IGNORE)
    assert cipher.encrypt("hello", key=3, options=opts) == "KHOOR"
    assert cipher.encrypt("HELLO", key=3, options=opts) == "KHOOR"


@pytest.mark.unit
def test_caesar_unknown_symbol_strip() -> None:
    cipher = CaesarCipher()
    options = TransformOptions(unknown_symbol_strategy=UnknownSymbolStrategy.STRIP)
    assert cipher.encrypt("Hello, World! 123", key=3, options=options) == "KhoorZruog"


@pytest.mark.unit
def test_caesar_unknown_symbol_reject() -> None:
    cipher = CaesarCipher()
    options = TransformOptions(unknown_symbol_strategy=UnknownSymbolStrategy.REJECT)
    with pytest.raises(UnknownSymbolError, match="outside alphabet"):
        cipher.encrypt("Hello, World!", key=3, options=options)

    # Clean input without unknown symbols succeeds
    assert cipher.encrypt("HelloWorld", key=3, options=options) == "KhoorZruog"


@pytest.mark.unit
def test_caesar_custom_alphabet_numeric() -> None:
    cipher = CaesarCipher()
    digits_alphabet = Alphabet("0123456789")
    options = TransformOptions(alphabet=digits_alphabet)
    assert cipher.encrypt("1239", key=2, options=options) == "3451"
    assert cipher.decrypt("3451", key=2, options=options) == "1239"


@pytest.mark.unit
def test_caesar_unicode_boundary_normalization() -> None:
    cipher = CaesarCipher()
    # Decomposed vs composed unicode characters in text
    decomposed = "Cafe\u0301 1914"
    composed = "Café 1914"
    assert cipher.encrypt(decomposed, key=3) == cipher.encrypt(composed, key=3)


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "ATTACK AT DAWN",
        "The quick brown fox jumps over the lazy dog 1234!?",
        "VENI, VIDI, VICI.",
        "Secret message with mixed casing: AaBbCcXxYyZz",
        "Short",
        "A",
        "",
    ],
)
@pytest.mark.parametrize("shift", [-100, -29, -26, -3, -1, 0, 1, 3, 26, 29, 100])
def test_caesar_round_trip_invariant(text: str, shift: int) -> None:
    cipher = CaesarCipher()
    options = TransformOptions(
        case_strategy=CaseStrategy.PRESERVE,
        unknown_symbol_strategy=UnknownSymbolStrategy.PASS_THROUGH,
    )
    encrypted = cipher.encrypt(text, key=shift, options=options)
    decrypted = cipher.decrypt(encrypted, key=shift, options=options)
    assert decrypted == text
