"""Tests for cipher catalog implementations."""

from random import Random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codebreakers.domain.ciphers.homophonic import (
    HomophonicKey,
    HomophonicSubstitutionCipher,
)
from codebreakers.domain.ciphers.substitution import SubstitutionCipher, SubstitutionKey
from codebreakers.domain.ciphers.vigenere import VigenereCipher, VigenereKey
from codebreakers.domain.errors import (
    AmbiguousKeyError,
    DuplicateKeyMappingError,
    IncompleteKeyError,
    InvalidKeyError,
)
from codebreakers.domain.models import Alphabet, CaseStrategy, TransformOptions

ALPHABET_TEXT = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SUBSTITUTION_ALPHABET = "QWERTYUIOPASDFGHJKLZXCVBNM"
SAMPLE_HOMOPHONIC_MAPPING = {
    symbol: (f"{index:02d}", f"{index + 100:03d}")
    for index, symbol in enumerate(ALPHABET_TEXT, start=1)
}


@pytest.mark.unit
def test_substitution_encrypts_and_decrypts() -> None:
    cipher = SubstitutionCipher()
    key = SubstitutionKey.from_cipher_alphabet(
        Alphabet.standard_latin(), SUBSTITUTION_ALPHABET
    )

    ciphertext = cipher.encrypt("ATTACK AT DAWN!", key)

    assert ciphertext == "QZZQEA QZ RQVF!"
    assert cipher.decrypt(ciphertext, key) == "ATTACK AT DAWN!"


@pytest.mark.unit
def test_substitution_rejects_malformed_keys() -> None:
    alphabet = Alphabet.standard_latin()

    with pytest.raises(IncompleteKeyError):
        SubstitutionKey.from_cipher_alphabet(alphabet, "ABC")
    with pytest.raises(DuplicateKeyMappingError):
        SubstitutionKey.from_cipher_alphabet(alphabet, "A" * 26)
    with pytest.raises(InvalidKeyError):
        SubstitutionKey.from_cipher_alphabet(alphabet, ALPHABET_TEXT[:-1] + "!")
    with pytest.raises(DuplicateKeyMappingError):
        SubstitutionKey.from_pairs(alphabet, [("A", "B"), ("A", "C")])


@pytest.mark.unit
def test_vigenere_encrypts_and_decrypts_known_example() -> None:
    cipher = VigenereCipher()
    key = VigenereKey.from_keyword("LEMON")

    ciphertext = cipher.encrypt("ATTACK AT DAWN", key)

    assert ciphertext == "LXFOPV EF RNHR"
    assert cipher.decrypt(ciphertext, key) == "ATTACK AT DAWN"


@pytest.mark.unit
def test_vigenere_advances_key_only_on_alphabet_symbols() -> None:
    cipher = VigenereCipher()
    key = VigenereKey.from_keyword("B")

    assert cipher.encrypt("A-A", key) == "B-B"


@pytest.mark.unit
def test_vigenere_rejects_invalid_keywords() -> None:
    with pytest.raises(InvalidKeyError):
        VigenereKey.from_keyword("")
    with pytest.raises(InvalidKeyError):
        VigenereKey.from_keyword("A!")


@pytest.mark.unit
def test_homophonic_encryption_can_be_seeded_and_decrypts_deterministically() -> None:
    key = HomophonicKey.from_mapping(
        Alphabet.standard_latin(), SAMPLE_HOMOPHONIC_MAPPING
    )
    first_cipher = HomophonicSubstitutionCipher(random_source=Random(7))
    second_cipher = HomophonicSubstitutionCipher(random_source=Random(7))

    first_ciphertext = first_cipher.encrypt("ABC", key)
    second_ciphertext = second_cipher.encrypt("ABC", key)

    assert first_ciphertext == second_ciphertext
    assert first_cipher.decrypt(first_ciphertext, key) == "ABC"
    assert second_cipher.decrypt(second_ciphertext, key) == "ABC"


@pytest.mark.unit
def test_homophonic_default_encryption_is_not_forced_deterministic() -> None:
    key = HomophonicKey.from_mapping(
        Alphabet.standard_latin(), SAMPLE_HOMOPHONIC_MAPPING
    )
    cipher = HomophonicSubstitutionCipher()

    samples = {cipher.encrypt("AAAAAAAAAAAA", key) for _ in range(20)}

    assert len(samples) > 1


@pytest.mark.unit
def test_homophonic_rejects_invalid_mappings() -> None:
    alphabet = Alphabet("ABC")

    with pytest.raises(IncompleteKeyError):
        HomophonicKey.from_mapping(alphabet, {"A": ["11"], "B": ["22"]})
    with pytest.raises(AmbiguousKeyError):
        HomophonicKey.from_mapping(alphabet, {"A": ["11"], "B": ["11"], "C": ["33"]})
    with pytest.raises(InvalidKeyError):
        HomophonicKey.from_mapping(alphabet, {"A": [], "B": ["22"], "C": ["33"]})


@given(st.text(alphabet=ALPHABET_TEXT + " .,!?", max_size=80), st.integers())
@pytest.mark.unit
def test_substitution_round_trip_property(text: str, shift: int) -> None:
    rotated = ALPHABET_TEXT[shift % 26 :] + ALPHABET_TEXT[: shift % 26]
    cipher = SubstitutionCipher()
    key = SubstitutionKey.from_cipher_alphabet(Alphabet.standard_latin(), rotated)
    options = TransformOptions(case_strategy=CaseStrategy.UPPERCASE)

    encrypted = cipher.encrypt(text, key, options)

    assert cipher.decrypt(encrypted, key, options) == text.upper()


@given(st.text(alphabet=ALPHABET_TEXT + " .,!?", max_size=80))
@pytest.mark.unit
def test_vigenere_round_trip_property(text: str) -> None:
    cipher = VigenereCipher()
    options = TransformOptions(case_strategy=CaseStrategy.UPPERCASE)
    key = VigenereKey.from_keyword("LEMON", options)

    encrypted = cipher.encrypt(text, key, options)

    assert cipher.decrypt(encrypted, key, options) == text.upper()


@given(st.text(alphabet=ALPHABET_TEXT, max_size=40))
@pytest.mark.unit
def test_homophonic_round_trip_property(text: str) -> None:
    key = HomophonicKey.from_mapping(
        Alphabet.standard_latin(), SAMPLE_HOMOPHONIC_MAPPING
    )
    cipher = HomophonicSubstitutionCipher(random_source=Random(11))

    encrypted = cipher.encrypt(text, key)

    assert cipher.decrypt(encrypted, key) == text
