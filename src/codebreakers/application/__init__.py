"""Application layer for Codebreakers."""

from __future__ import annotations

__all__ = [
    "CipherOperation",
    "CipherRequest",
    "CipherService",
]


def __getattr__(name: str) -> object:
    """Lazily expose application-layer API without eager imports."""
    if name in {"CipherOperation", "CipherRequest", "CipherService"}:
        from codebreakers.application.services import (
            CipherOperation,
            CipherRequest,
            CipherService,
        )

        return {
            "CipherOperation": CipherOperation,
            "CipherRequest": CipherRequest,
            "CipherService": CipherService,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
