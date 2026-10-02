"""Budget enforcement tests for analysis executors, using real child processes."""

import os
import time
from datetime import timedelta

import pytest

from codebreakers.application.analysis import AnalysisOutcome
from codebreakers.application.errors import (
    AnalysisBudgetExceededError,
    AnalysisInterruptedError,
    AnalysisRejectedError,
    UnsupportedAnalyzerError,
)
from codebreakers.application.processing import ExecutionBudget
from codebreakers.domain.cryptanalysis.models import AnalysisResult
from codebreakers.domain.errors import InsufficientTextError, UnknownSymbolError
from codebreakers.worker.execution import (
    ANALYSIS_FAILED,
    MEMORY_BUDGET_EXCEEDED,
    TIME_BUDGET_EXCEEDED,
    InlineAnalysisExecutor,
    ProcessAnalysisExecutor,
    error_code_for,
)

BUDGET = ExecutionBudget(time_limit=timedelta(seconds=10))


# Child processes are spawned, so these helpers must be importable top-level names.
def sleepy_analysis(_analyzer: str, _language: str, _text: str) -> AnalysisOutcome:
    time.sleep(30)
    raise AssertionError("unreachable")


def crashing_analysis(_analyzer: str, _language: str, _text: str) -> AnalysisOutcome:
    os._exit(3)


def memory_hungry_analysis(
    _analyzer: str, _language: str, _text: str
) -> AnalysisOutcome:
    raise MemoryError


def buggy_analysis(_analyzer: str, _language: str, text: str) -> AnalysisOutcome:
    raise RuntimeError(text)


@pytest.mark.unit
def test_process_executor_returns_registered_analysis_outcome() -> None:
    outcome = ProcessAnalysisExecutor().execute(
        "caesar-bruteforce",
        "english",
        "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ",
        BUDGET,
    )
    assert isinstance(outcome, AnalysisResult)
    assert outcome.candidates[0].key == "3"


@pytest.mark.unit
def test_process_executor_reports_domain_failures_as_rejections() -> None:
    with pytest.raises(AnalysisRejectedError) as caught:
        ProcessAnalysisExecutor().execute(
            "vigenere-frequency", "english", "SHORT", BUDGET
        )
    assert caught.value.error_code == "insufficient-text"


@pytest.mark.unit
def test_process_executor_kills_analysis_that_exceeds_its_time_budget() -> None:
    budget = ExecutionBudget(time_limit=timedelta(seconds=1))
    started = time.monotonic()
    with pytest.raises(AnalysisBudgetExceededError) as caught:
        ProcessAnalysisExecutor(sleepy_analysis).execute("a", "b", "c", budget)
    assert caught.value.error_code == TIME_BUDGET_EXCEEDED
    assert time.monotonic() - started < 10


@pytest.mark.unit
def test_process_executor_reports_memory_exhaustion_as_budget_overrun() -> None:
    with pytest.raises(AnalysisBudgetExceededError) as caught:
        ProcessAnalysisExecutor(memory_hungry_analysis).execute("a", "b", "c", BUDGET)
    assert caught.value.error_code == MEMORY_BUDGET_EXCEEDED


@pytest.mark.unit
def test_process_executor_treats_unexpected_child_exit_as_retryable() -> None:
    with pytest.raises(AnalysisInterruptedError, match=r"\(3\)"):
        ProcessAnalysisExecutor(crashing_analysis).execute("a", "b", "c", BUDGET)


@pytest.mark.unit
def test_process_executor_never_reports_input_in_errors() -> None:
    with pytest.raises(AnalysisRejectedError) as caught:
        ProcessAnalysisExecutor(buggy_analysis).execute("a", "b", "SECRET", BUDGET)
    assert caught.value.error_code == ANALYSIS_FAILED
    assert "SECRET" not in str(caught.value)


@pytest.mark.unit
def test_inline_executor_translates_failures() -> None:
    executor = InlineAnalysisExecutor()
    assert isinstance(
        executor.execute("caesar-bruteforce", "english", "KHOOR", BUDGET),
        AnalysisResult,
    )
    with pytest.raises(AnalysisRejectedError) as caught:
        executor.execute("nope", "english", "KHOOR", BUDGET)
    assert caught.value.error_code == "unsupported-analyzer"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (InsufficientTextError("short"), "insufficient-text"),
        (UnknownSymbolError("?"), "unknown-symbol"),
        (UnsupportedAnalyzerError("x", ["y"]), "unsupported-analyzer"),
        (RuntimeError("boom"), ANALYSIS_FAILED),
    ],
)
def test_error_codes_are_stable(error: Exception, code: str) -> None:
    assert error_code_for(error) == code
