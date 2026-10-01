"""Application-level error definitions for Codebreakers."""

from collections.abc import Iterable

from codebreakers.domain.errors import CodebreakersError


class ApplicationError(CodebreakersError):
    """Base exception for application-layer errors."""


class UnsupportedCapabilityError(ApplicationError):
    """Raised when a requested registry entry does not exist."""

    kind = "capability"

    def __init__(self, name: str, supported: Iterable[str]) -> None:
        self.name = name
        self.supported = tuple(sorted(supported))
        super().__init__(
            f"Unsupported {self.kind} '{name}'. "
            f"Supported {self.kind}s: {', '.join(self.supported)}."
        )


class UnsupportedCipherError(UnsupportedCapabilityError):
    """Raised when a cipher name is not registered."""

    kind = "cipher"


class UnsupportedAnalyzerError(UnsupportedCapabilityError):
    """Raised when an analyzer name is not registered."""

    kind = "analyzer"


class UnsupportedLanguageError(UnsupportedCapabilityError):
    """Raised when a language model name is not registered."""

    kind = "language"


class AnalysisNotFoundError(ApplicationError):
    """Raised when an analysis job cannot be found."""


class ConcurrentAnalysisUpdateError(ApplicationError):
    """Raised when an analysis job changed since it was read."""
