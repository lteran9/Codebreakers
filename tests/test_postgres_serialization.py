"""Tests for PostgreSQL persistence serialization."""

import json
import math

import pytest

from codebreakers.domain.cryptanalysis.models import AnalysisResult, Candidate
from codebreakers.infrastructure.persistence.postgres import (
    _decode_outcome,
    _encode_outcome,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (math.nan, math.nan),
        (math.inf, math.inf),
        (-math.inf, -math.inf),
    ],
)
def test_nonfinite_scores_round_trip_through_json(
    score: float, expected: float
) -> None:
    outcome = AnalysisResult(
        analyzer="test",
        language="english",
        language_version="test-v1",
        candidates=(
            Candidate(
                rank=1,
                score=score,
                key="3",
                text="PLAINTEXT",
                explanation="test result",
            ),
        ),
    )

    persisted = json.loads(json.dumps(_encode_outcome(outcome), allow_nan=False))
    restored = _decode_outcome(persisted)

    assert isinstance(restored, AnalysisResult)
    actual = restored.candidates[0].score
    if math.isnan(expected):
        assert math.isnan(actual)
    else:
        assert actual == expected
