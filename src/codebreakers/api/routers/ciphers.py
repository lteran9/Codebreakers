"""Cipher encrypt and decrypt routes."""

from typing import Annotated

from fastapi import APIRouter, Path

from codebreakers.api.dependencies import CipherRunnerDep
from codebreakers.api.problems import problem_responses
from codebreakers.api.schemas import (
    MAX_NAME_LENGTH,
    CipherOperationRequest,
    CipherOperationResponse,
)
from codebreakers.application.services import CipherOperation
from codebreakers.composition import CIPHER_REGISTRY

router = APIRouter(prefix="/ciphers", tags=["ciphers"])

CipherName = Annotated[
    str,
    Path(
        max_length=MAX_NAME_LENGTH,
        description=f"Registered cipher. Supported: {', '.join(CIPHER_REGISTRY)}.",
        examples=["caesar"],
    ),
]

_RESPONSES = problem_responses(404, 413, 422)


@router.post(
    "/{cipher}/encrypt",
    operation_id="encrypt_text",
    summary="Encrypt text",
    responses=_RESPONSES,
)
def encrypt_text(
    cipher: CipherName, body: CipherOperationRequest, run: CipherRunnerDep
) -> CipherOperationResponse:
    """Encrypt text with the named cipher and key."""
    result = run(cipher, CipherOperation.ENCRYPT, body.text, body.key, body.alphabet)
    return CipherOperationResponse(cipher=cipher, operation="encrypt", result=result)


@router.post(
    "/{cipher}/decrypt",
    operation_id="decrypt_text",
    summary="Decrypt text",
    responses=_RESPONSES,
)
def decrypt_text(
    cipher: CipherName, body: CipherOperationRequest, run: CipherRunnerDep
) -> CipherOperationResponse:
    """Decrypt text with the named cipher and key."""
    result = run(cipher, CipherOperation.DECRYPT, body.text, body.key, body.alphabet)
    return CipherOperationResponse(cipher=cipher, operation="decrypt", result=result)
