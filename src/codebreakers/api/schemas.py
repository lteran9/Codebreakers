"""Pydantic request and response schemas for the HTTP API."""

import math
from datetime import datetime
from typing import Annotated, Any, Literal, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codebreakers.application.analysis import (
    AnalysisJob,
    AnalysisOutcome,
    AnalysisStatus,
)
from codebreakers.composition import ANALYZER_REGISTRY, LANGUAGE_MODELS
from codebreakers.domain.cryptanalysis.analyzers import VigenereAnalysisResult
from codebreakers.domain.cryptanalysis.models import (
    AnalysisResult,
    FrequencyAnalysisReport,
)
from codebreakers.domain.models import Alphabet

MAX_TEXT_LENGTH = 20_000
MAX_KEY_LENGTH = 4_096
MAX_ALPHABET_LENGTH = 1_024
MAX_NAME_LENGTH = 64
DEFAULT_ALPHABET = Alphabet.standard_latin().symbols
DEFAULT_LANGUAGE = "english"


_CAESAR_EXAMPLE_CIPHERTEXT = (
    "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ DQG NHHSV UXQQLQJ WKURXJK WKH ILHOG"
)
_CAESAR_EXAMPLE_PLAINTEXT = (
    "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG AND KEEPS RUNNING THROUGH THE FIELD"
)


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CipherOperationRequest(_StrictRequest):
    """Text, raw key, and alphabet for an encrypt or decrypt operation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"text": "HELLO WORLD", "key": "3"}],
        }
    )

    text: str = Field(
        max_length=MAX_TEXT_LENGTH, description="Plaintext or ciphertext to transform."
    )
    key: str = Field(
        min_length=1,
        max_length=MAX_KEY_LENGTH,
        description=(
            "Cipher key as a string: an integer shift (caesar), a keyword "
            "(vigenere), a cipher alphabet (substitution), or a JSON object "
            "(homophonic)."
        ),
    )
    alphabet: str = Field(
        default=DEFAULT_ALPHABET,
        max_length=MAX_ALPHABET_LENGTH,
        description="Unique alphabet symbols used by the cipher.",
    )


class CipherOperationResponse(BaseModel):
    """Result of an encrypt or decrypt operation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"cipher": "caesar", "operation": "encrypt", "result": "KHOOR ZRUOG"}
            ]
        }
    )

    cipher: str
    operation: Literal["encrypt", "decrypt"]
    result: str


class AnalysisRequest(_StrictRequest):
    """Ciphertext and analyzer selection for a keyless analysis."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "analyzer": "caesar-bruteforce",
                    "text": _CAESAR_EXAMPLE_CIPHERTEXT,
                    "language": DEFAULT_LANGUAGE,
                }
            ]
        }
    )

    analyzer: str = Field(
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description=f"Analyzer to run. Supported: {', '.join(ANALYZER_REGISTRY)}.",
    )
    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="Ciphertext to analyze.",
    )
    language: str = Field(
        default=DEFAULT_LANGUAGE,
        min_length=1,
        max_length=MAX_NAME_LENGTH,
        description=(
            f"Language model for scoring. Supported: {', '.join(LANGUAGE_MODELS)}."
        ),
    )


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _ResultModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CandidateModel(_ResultModel):
    """A ranked candidate key and plaintext."""

    rank: int
    score: float | None = Field(
        description="Language score; null when the text has no scorable symbols."
    )
    key: str
    text: str
    explanation: str

    _normalize_score = field_validator("score", mode="before")(_finite_or_none)


class KeyLengthCandidateModel(_ResultModel):
    """A ranked Vigenere key-length estimate."""

    rank: int
    key_length: int
    score: float
    explanation: str


class RankedCandidatesResult(_ResultModel):
    """Candidates ranked by language score."""

    kind: Literal["ranked-candidates"] = "ranked-candidates"
    analyzer: str
    language: str
    language_version: str
    candidates: list[CandidateModel]


class FrequencyReportResult(_ResultModel):
    """Symbol or token frequency report."""

    kind: Literal["frequency-report"] = "frequency-report"
    analyzer: str
    language: str
    language_version: str
    symbol_counts: dict[str, int]
    symbol_frequencies: dict[str, float]
    comparisons: dict[str, float]
    notes: list[str]


class VigenereKeyAnalysisResult(_ResultModel):
    """Vigenere key-length estimates and candidate keys."""

    kind: Literal["vigenere-key-analysis"] = "vigenere-key-analysis"
    analyzer: str
    language: str
    language_version: str
    key_lengths: list[KeyLengthCandidateModel]
    candidate_keys: list[CandidateModel]
    minimum_useful_ciphertext_length: int
    limitations: list[str]


AnalysisResultModel = Annotated[
    RankedCandidatesResult | FrequencyReportResult | VigenereKeyAnalysisResult,
    Field(discriminator="kind"),
]


class AnalysisJobResponse(BaseModel):
    """An analysis job resource."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "0b8f6a3e-2d43-4c1e-9f55-6a1d2b7c9e10",
                    "status": "succeeded",
                    "analyzer": "caesar-bruteforce",
                    "language": "english",
                    "created_at": "2026-09-30T12:00:00Z",
                    "updated_at": "2026-09-30T12:00:00.004Z",
                    "completed_at": "2026-09-30T12:00:00.004Z",
                    "version": 3,
                    "result": {
                        "kind": "ranked-candidates",
                        "analyzer": "caesar-bruteforce",
                        "language": "english",
                        "language_version": "letter-frequency-v1",
                        "candidates": [
                            {
                                "rank": 1,
                                "score": -69.26,
                                "key": "3",
                                "text": _CAESAR_EXAMPLE_PLAINTEXT,
                                "explanation": (
                                    "Shift 3 scored -69.2637 against english."
                                ),
                            }
                        ],
                    },
                }
            ]
        }
    )

    id: UUID
    status: AnalysisStatus
    analyzer: str
    language: str
    created_at: datetime
    updated_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    version: int
    result: AnalysisResultModel | None

    @classmethod
    def from_job(cls, job: AnalysisJob) -> "AnalysisJobResponse":
        """Translate an application job into its HTTP representation."""
        return cls(
            id=job.id,
            status=job.status,
            analyzer=job.analyzer,
            language=job.language,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            error_code=job.error_code,
            version=job.version,
            result=None if job.result is None else _result_model(job.result),
        )


class AnalysisJobPageResponse(BaseModel):
    """One paginated collection of analysis jobs."""

    items: list[AnalysisJobResponse]
    total: int
    offset: int
    limit: int


def _result_model(
    outcome: AnalysisOutcome,
) -> RankedCandidatesResult | FrequencyReportResult | VigenereKeyAnalysisResult:
    match outcome:
        case AnalysisResult():
            return RankedCandidatesResult.model_validate(outcome)
        case FrequencyAnalysisReport():
            return FrequencyReportResult.model_validate(outcome)
        case VigenereAnalysisResult():
            return VigenereKeyAnalysisResult.model_validate(outcome)
        case _:
            assert_never(outcome)


class LivenessResponse(BaseModel):
    """Process liveness."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Dependency readiness with per-check results."""

    status: Literal["ready", "not-ready"]
    checks: dict[str, Literal["ok", "failing"]]
