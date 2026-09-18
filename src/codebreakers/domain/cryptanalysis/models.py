"""Typed cryptanalysis protocols and result models."""

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

CandidateT_co = TypeVar("CandidateT_co", covariant=True)


class Analyzer(Protocol[CandidateT_co]):
    """Protocol for analysis capabilities separate from encryption ciphers."""

    def analyze(self, text: str) -> CandidateT_co:
        """Analyze ciphertext and return an explainable result."""
        ...


@dataclass(frozen=True, slots=True)
class Candidate:
    """Ranked candidate plaintext or key result."""

    rank: int
    score: float
    key: str
    text: str
    explanation: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Ranked cryptanalysis result set."""

    analyzer: str
    language: str
    language_version: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class LanguageModel:
    """Language scoring configuration for frequency-based analyzers."""

    name: str
    version: str
    symbol_frequencies: dict[str, float]


@dataclass(frozen=True, slots=True)
class FrequencyAnalysisReport:
    """Explainable frequency-analysis report."""

    analyzer: str
    language: str
    language_version: str
    symbol_counts: dict[str, int]
    symbol_frequencies: dict[str, float]
    comparisons: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
