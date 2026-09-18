"""Tests for cryptanalysis capabilities."""

import json
from pathlib import Path

import pytest

from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.ciphers.vigenere import VigenereCipher, VigenereKey
from codebreakers.domain.cryptanalysis.analyzers import (
    CaesarBruteForceAnalyzer,
    HomophonicDistributionAnalyzer,
    SubstitutionFrequencyAnalyzer,
    VigenereAnalyzer,
)
from codebreakers.domain.cryptanalysis.statistics import (
    index_of_coincidence,
    ngram_counts,
    score_english_text,
    symbol_counts,
)
from codebreakers.domain.errors import InsufficientTextError

FIXTURE_PATH = Path("tests/fixtures/historical_examples/v1/classical_ciphers.json")


@pytest.mark.unit
def test_symbol_and_ngram_counts_are_deterministic() -> None:
    assert symbol_counts("ABBA!") == {"A": 2, "B": 2}
    assert ngram_counts("ABBA", 2) == {"AB": 1, "BB": 1, "BA": 1}
    assert index_of_coincidence("AAAA") == 1.0
    assert index_of_coincidence("A") == 0.0
    english_score = score_english_text("THIS IS ENGLISH TEXT")
    unlikely_score = score_english_text("ZXQJ KV XQZJJ")
    assert english_score > unlikely_score


@pytest.mark.unit
def test_ngram_counts_reject_invalid_size() -> None:
    with pytest.raises(ValueError):
        ngram_counts("ABC", 0)


@pytest.mark.unit
def test_caesar_bruteforce_ranks_documented_example() -> None:
    plaintext = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG"
    ciphertext = CaesarCipher().encrypt(plaintext, 3)
    result = CaesarBruteForceAnalyzer().analyze(ciphertext)

    assert len(result.candidates) == 26
    assert result.language == "english"
    assert result.candidates[0].key == "3"
    assert result.candidates[0].text == "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG"
    assert result.candidates == tuple(
        sorted(result.candidates, key=lambda candidate: candidate.score, reverse=True)
    )


@pytest.mark.unit
def test_substitution_frequency_analysis_reports_measurements() -> None:
    report = SubstitutionFrequencyAnalyzer().analyze("ABBCCC")

    assert report.analyzer == "substitution-frequency"
    assert report.symbol_counts == {"A": 1, "B": 2, "C": 3}
    assert report.symbol_frequencies["C"] == 0.5
    assert report.comparisons
    assert "does not claim" in report.notes[0]


@pytest.mark.unit
def test_vigenere_analyzer_returns_ranked_key_lengths_and_candidates() -> None:
    plaintext = "THEQUICKBROWNFOXJUMPSOVERTHELAZYDOG" * 4
    options = None
    ciphertext = VigenereCipher().encrypt(
        plaintext, VigenereKey.from_keyword("KEY"), options
    )

    result = VigenereAnalyzer(max_key_length=8).analyze(ciphertext)

    assert result.language == "english"
    assert result.key_lengths
    assert result.candidate_keys
    assert result.key_lengths == tuple(
        sorted(result.key_lengths, key=lambda candidate: candidate.score, reverse=True)
    )
    assert any(candidate.key_length == 3 for candidate in result.key_lengths)
    assert result.minimum_useful_ciphertext_length == 12
    assert result.limitations


@pytest.mark.unit
def test_vigenere_analyzer_rejects_insufficient_text() -> None:
    with pytest.raises(InsufficientTextError):
        VigenereAnalyzer().analyze("ABC")


@pytest.mark.unit
def test_homophonic_distribution_report_is_deterministic() -> None:
    report = HomophonicDistributionAnalyzer().analyze("11 22 11 33")

    assert report.analyzer == "homophonic-distribution"
    assert report.symbol_counts == {"11": 2, "22": 1, "33": 1}
    assert report.symbol_frequencies == {"11": 0.5, "22": 0.25, "33": 0.25}
    assert "does not automatically solve" in report.notes[0]


@pytest.mark.unit
def test_versioned_historical_fixtures_are_traceable_and_offline() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixtures["version"] == 1
    assert fixtures["fixtures"]
    for fixture in fixtures["fixtures"]:
        assert fixture["provenance"]
        assert fixture["license"]
        assert fixture["plaintext"]
        assert fixture["ciphertext"]
