"""Unit tests for the application-layer CipherService."""

import pytest

from codebreakers.application.services import (
    CipherOperation,
    CipherRequest,
    CipherService,
)
from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.models import TransformOptions


class FakeCipher:
    """Fake cipher to verify CipherService depends only on protocol abstraction."""

    def encrypt(
        self,
        text: str,
        key: str,
        options: TransformOptions | None = None,
    ) -> str:
        return f"ENCRYPTED_{text}_{key}"

    def decrypt(
        self,
        ciphertext: str,
        key: str,
        options: TransformOptions | None = None,
    ) -> str:
        return f"DECRYPTED_{ciphertext}_{key}"


@pytest.mark.unit
def test_cipher_service_with_fake_cipher() -> None:
    service = CipherService()
    fake = FakeCipher()

    encrypt_req = CipherRequest(
        text="secret",
        key="KEY123",
        operation=CipherOperation.ENCRYPT,
    )
    assert service.process(fake, encrypt_req) == "ENCRYPTED_secret_KEY123"

    decrypt_req = CipherRequest(
        text="secret",
        key="KEY123",
        operation=CipherOperation.DECRYPT,
    )
    assert service.process(fake, decrypt_req) == "DECRYPTED_secret_KEY123"


@pytest.mark.unit
def test_cipher_service_convenience_methods() -> None:
    service = CipherService()
    fake = FakeCipher()

    assert service.encrypt(fake, text="hello", key="ABC") == "ENCRYPTED_hello_ABC"
    assert service.decrypt(fake, ciphertext="hello", key="ABC") == "DECRYPTED_hello_ABC"


@pytest.mark.unit
def test_cipher_service_with_caesar() -> None:
    service = CipherService()
    caesar = CaesarCipher()

    request = CipherRequest(
        text="HELLO",
        key=3,
        operation=CipherOperation.ENCRYPT,
    )
    assert service.process(caesar, request) == "KHOOR"
