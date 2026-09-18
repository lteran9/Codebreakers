"""Cryptanalysis analyzers for classical ciphers."""

from dataclasses import dataclass

from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.ciphers.vigenere import VigenereCipher, VigenereKey
from codebreakers.domain.cryptanalysis.models import (
    AnalysisResult,
    Candidate,
    FrequencyAnalysisReport,
    LanguageModel,
)
from codebreakers.domain.cryptanalysis.statistics import (
    ENGLISH_LANGUAGE_MODEL,
    index_of_coincidence,
    score_english_text,
    symbol_counts,
    symbol_frequencies,
)
from codebreakers.domain.errors import InsufficientTextError
from codebreakers.domain.models import (
    Alphabet,
    CaseStrategy,
    TransformOptions,
)
from codebreakers.domain.text import normalize_text


class CaesarBruteForceAnalyzer:
    """Rank all Caesar shifts by configurable language score."""

    def __init__(
        self,
        language_model: LanguageModel | None = None,
        options: TransformOptions | None = None,
    ) -> None:
        self._language_model = language_model or ENGLISH_LANGUAGE_MODEL
        self._options = options or TransformOptions(
            case_strategy=CaseStrategy.UPPERCASE
        )
        self._cipher = CaesarCipher()

    def analyze(self, text: str) -> AnalysisResult:
        """Return a ranked candidate for every valid Caesar shift."""
        candidates: list[Candidate] = []
        for shift in range(len(self._options.alphabet)):
            plaintext = self._cipher.decrypt(text, shift, self._options)
            score = score_english_text(
                plaintext, self._language_model, self._options.alphabet
            )
            candidates.append(
                Candidate(
                    rank=0,
                    score=score,
                    key=str(shift),
                    text=plaintext,
                    explanation=(
                        f"Shift {shift} scored {score:.4f} "
                        f"against {self._language_model.name}."
                    ),
                )
            )
        ranked = tuple(
            Candidate(
                rank=rank,
                score=candidate.score,
                key=candidate.key,
                text=candidate.text,
                explanation=candidate.explanation,
            )
            for rank, candidate in enumerate(
                sorted(candidates, key=lambda item: item.score, reverse=True), start=1
            )
        )
        return AnalysisResult(
            analyzer="caesar-bruteforce",
            language=self._language_model.name,
            language_version=self._language_model.version,
            candidates=ranked,
        )


class SubstitutionFrequencyAnalyzer:
    """Frequency-analysis assistance for substitution ciphertext."""

    def __init__(
        self,
        language_model: LanguageModel | None = None,
        alphabet: Alphabet | None = None,
    ) -> None:
        self._language_model = language_model or ENGLISH_LANGUAGE_MODEL
        self._alphabet = alphabet or Alphabet.standard_latin()

    def analyze(self, text: str) -> FrequencyAnalysisReport:
        """Return deterministic symbol frequencies and language comparisons."""
        observed = symbol_frequencies(text, self._alphabet)
        comparisons = {
            symbol: observed.get(symbol, 0.0)
            - self._language_model.symbol_frequencies.get(symbol, 0.0)
            for symbol in self._alphabet.symbols
        }
        return FrequencyAnalysisReport(
            analyzer="substitution-frequency",
            language=self._language_model.name,
            language_version=self._language_model.version,
            symbol_counts=symbol_counts(text, self._alphabet),
            symbol_frequencies=observed,
            comparisons=comparisons,
            notes=(
                "This report assists analysis and does not claim to solve "
                "arbitrary substitution ciphertext.",
            ),
        )


@dataclass(frozen=True, slots=True)
class VigenereKeyLengthCandidate:
    """Ranked candidate Vigenere key length."""

    rank: int
    key_length: int
    score: float
    explanation: str


@dataclass(frozen=True, slots=True)
class VigenereAnalysisResult:
    """Vigenere key-length and candidate-key analysis result."""

    analyzer: str
    language: str
    language_version: str
    key_lengths: tuple[VigenereKeyLengthCandidate, ...]
    candidate_keys: tuple[Candidate, ...]
    minimum_useful_ciphertext_length: int
    limitations: tuple[str, ...]


class VigenereAnalyzer:
    """Estimate Vigenere key lengths and candidate keys using frequency scores."""

    def __init__(
        self,
        language_model: LanguageModel | None = None,
        options: TransformOptions | None = None,
        max_key_length: int = 12,
    ) -> None:
        if max_key_length < 1:
            msg = "max_key_length must be at least 1."
            raise ValueError(msg)
        self._language_model = language_model or ENGLISH_LANGUAGE_MODEL
        self._options = options or TransformOptions(
            case_strategy=CaseStrategy.UPPERCASE
        )
        self._max_key_length = max_key_length
        self._cipher = VigenereCipher()

    def analyze(self, text: str) -> VigenereAnalysisResult:
        """Return ranked key lengths and coarse candidate keys."""
        normalized = normalize_text(text)
        supported_symbols: list[str] = []
        for symbol in normalized:
            if symbol in self._options.alphabet:
                supported_symbols.append(symbol)
            elif symbol.upper() in self._options.alphabet:
                supported_symbols.append(symbol.upper())
            elif symbol.lower() in self._options.alphabet:
                supported_symbols.append(symbol.lower())
        supported = "".join(supported_symbols)
        if len(supported) < 12:
            raise InsufficientTextError(
                "Vigenere analysis requires at least 12 supported ciphertext symbols."
            )

        key_lengths = self._rank_key_lengths(supported)
        candidate_keys = tuple(
            self._candidate_for_key_length(supported, candidate.key_length, rank)
            for rank, candidate in enumerate(
                key_lengths[: min(5, len(key_lengths))], start=1
            )
        )
        return VigenereAnalysisResult(
            analyzer="vigenere-frequency",
            language=self._language_model.name,
            language_version=self._language_model.version,
            key_lengths=key_lengths,
            candidate_keys=candidate_keys,
            minimum_useful_ciphertext_length=12,
            limitations=(
                "Short ciphertexts may not contain enough repeated structure "
                "for reliable key recovery.",
                "Candidate keys are frequency-ranked hints, not guaranteed solutions.",
            ),
        )

    def _rank_key_lengths(
        self, supported: str
    ) -> tuple[VigenereKeyLengthCandidate, ...]:
        candidates: list[VigenereKeyLengthCandidate] = []
        max_length = min(self._max_key_length, len(supported))
        for key_length in range(1, max_length + 1):
            columns = [supported[offset::key_length] for offset in range(key_length)]
            average_ic = sum(
                index_of_coincidence(column, self._options.alphabet)
                for column in columns
            ) / len(columns)
            candidates.append(
                VigenereKeyLengthCandidate(
                    rank=0,
                    key_length=key_length,
                    score=average_ic,
                    explanation=(
                        f"Average column index of coincidence: {average_ic:.4f}."
                    ),
                )
            )
        return tuple(
            VigenereKeyLengthCandidate(
                rank=rank,
                key_length=candidate.key_length,
                score=candidate.score,
                explanation=candidate.explanation,
            )
            for rank, candidate in enumerate(
                sorted(candidates, key=lambda item: item.score, reverse=True), start=1
            )
        )

    def _candidate_for_key_length(
        self,
        supported: str,
        key_length: int,
        rank: int,
    ) -> Candidate:
        key_symbols: list[str] = []
        for offset in range(key_length):
            column = supported[offset::key_length]
            best_shift = max(
                range(len(self._options.alphabet)),
                key=lambda shift: score_english_text(
                    CaesarCipher().decrypt(column, shift, self._options),
                    self._language_model,
                    self._options.alphabet,
                ),
            )
            key_symbols.append(self._options.alphabet.symbol_at(best_shift))
        key = "".join(key_symbols)
        plaintext = self._cipher.decrypt(
            supported, VigenereKey.from_keyword(key, self._options), self._options
        )
        score = score_english_text(
            plaintext, self._language_model, self._options.alphabet
        )
        return Candidate(
            rank=rank,
            score=score,
            key=key,
            text=plaintext,
            explanation=f"Candidate key for estimated key length {key_length}.",
        )


class HomophonicDistributionAnalyzer:
    """Token-distribution report for homophonic ciphertext."""

    def analyze(self, text: str) -> FrequencyAnalysisReport:
        """Return deterministic token distribution reports."""
        tokens = tuple(token for token in normalize_text(text).split() if token)
        token_counts: dict[str, int] = {}
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1
        total = sum(token_counts.values())
        frequencies = (
            {token: count / total for token, count in sorted(token_counts.items())}
            if total
            else {}
        )
        return FrequencyAnalysisReport(
            analyzer="homophonic-distribution",
            language="token-distribution",
            language_version="token-count-v1",
            symbol_counts=dict(sorted(token_counts.items())),
            symbol_frequencies=frequencies,
            notes=(
                "This report exposes token distributions and does not "
                "automatically solve homophonic ciphertext.",
            ),
        )
