"""Tests for the explicit cipher registry."""

import pytest

from codebreakers.application.services import CipherOperation
from codebreakers.composition import CIPHER_REGISTRY, get_cipher
from codebreakers.domain.errors import InvalidKeyError
from codebreakers.domain.models import Alphabet, TransformOptions


@pytest.mark.unit
def test_registry_contains_supported_ciphers() -> None:
    assert set(CIPHER_REGISTRY) == {
        "caesar",
        "homophonic",
        "substitution",
        "vigenere",
    }


@pytest.mark.unit
def test_registry_selects_caesar() -> None:
    spec = get_cipher("caesar")
    options = TransformOptions()

    encrypted = spec.process(CipherOperation.ENCRYPT, "HELLO", "3", options)

    assert encrypted == "KHOOR"
    assert spec.process(CipherOperation.DECRYPT, encrypted, "3", options) == "HELLO"


@pytest.mark.unit
def test_registry_selects_substitution() -> None:
    spec = get_cipher("substitution")
    options = TransformOptions()

    encrypted = spec.process(
        CipherOperation.ENCRYPT,
        "ABC",
        "QWERTYUIOPASDFGHJKLZXCVBNM",
        options,
    )

    assert encrypted == "QWE"


@pytest.mark.unit
def test_registry_selects_vigenere() -> None:
    spec = get_cipher("vigenere")
    options = TransformOptions()

    encrypted = spec.process(CipherOperation.ENCRYPT, "ATTACKATDAWN", "LEMON", options)

    assert encrypted == "LXFOPVEFRNHR"


@pytest.mark.unit
def test_registry_selects_homophonic() -> None:
    spec = get_cipher("homophonic")
    options = TransformOptions(alphabet=Alphabet("AB"))
    raw_key = '{"A":["11"],"B":["21"]}'

    encrypted = spec.process(CipherOperation.ENCRYPT, "AB", raw_key, options)

    assert encrypted == "11 21"
    assert spec.process(CipherOperation.DECRYPT, encrypted, raw_key, options) == "AB"


@pytest.mark.unit
def test_registry_unknown_cipher_is_intentional_key_error() -> None:
    with pytest.raises(KeyError):
        get_cipher("unknown")


@pytest.mark.unit
def test_registry_rejects_invalid_raw_key() -> None:
    spec = get_cipher("caesar")
    with pytest.raises(InvalidKeyError):
        spec.process(CipherOperation.ENCRYPT, "HELLO", "abc", TransformOptions())
