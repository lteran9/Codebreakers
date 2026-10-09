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


class AnalysisCapacityError(ApplicationError):
    """Raised when too many analyses are waiting or running to accept another."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"The service is processing its limit of {limit} analyses. Retry later."
        )


class ConcurrentAnalysisUpdateError(ApplicationError):
    """Raised when an analysis job changed since it was read."""


class InvalidJobMessageError(ApplicationError):
    """Raised when a queued job message does not match a supported schema."""


class AnalysisExecutionError(ApplicationError):
    """Base exception for failures reported by an analysis executor."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class AnalysisBudgetExceededError(AnalysisExecutionError):
    """Raised when an analysis exceeds its time or memory budget."""


class AnalysisRejectedError(AnalysisExecutionError):
    """Raised when an analysis fails permanently, so retrying cannot help."""


class AnalysisInterruptedError(AnalysisExecutionError):
    """Raised when an analysis stopped unexpectedly and may succeed if retried."""
