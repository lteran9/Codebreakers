"""Analysis executors that enforce per-job time and memory budgets."""

import math
import multiprocessing
import signal
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from typing import Any

from codebreakers.application.analysis import AnalysisOutcome
from codebreakers.application.errors import (
    AnalysisBudgetExceededError,
    AnalysisInterruptedError,
    AnalysisRejectedError,
    UnsupportedAnalyzerError,
    UnsupportedLanguageError,
)
from codebreakers.application.processing import ExecutionBudget
from codebreakers.composition import create_analysis_service
from codebreakers.domain.errors import (
    AlphabetError,
    CodebreakersError,
    InsufficientTextError,
    UnknownSymbolError,
)

type AnalysisFunction = Callable[[str, str, str], AnalysisOutcome]

TIME_BUDGET_EXCEEDED = "time-budget-exceeded"
MEMORY_BUDGET_EXCEEDED = "memory-budget-exceeded"
ANALYSIS_FAILED = "analysis-failed"
ANALYSIS_INTERRUPTED = "analysis-interrupted"

# RLIMIT_CPU delivers SIGXCPU; the attribute is absent on non-POSIX platforms.
_SIGXCPU: int | None = getattr(signal, "SIGXCPU", None)

_ERROR_CODES: tuple[tuple[type[CodebreakersError], str], ...] = (
    (InsufficientTextError, "insufficient-text"),
    (UnsupportedAnalyzerError, "unsupported-analyzer"),
    (UnsupportedLanguageError, "unsupported-language"),
    (UnknownSymbolError, "unknown-symbol"),
    (AlphabetError, "invalid-alphabet"),
)


def run_registered_analysis(analyzer: str, language: str, text: str) -> AnalysisOutcome:
    """Run an analyzer from the shared registry, as the CLI and API do."""
    return create_analysis_service().analyze(analyzer, text, language)


def error_code_for(error: BaseException) -> str:
    """Map an analysis failure to a stable code that never includes its input."""
    for error_type, code in _ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return ANALYSIS_FAILED


class InlineAnalysisExecutor:
    """Run analyses in the calling thread without enforcing a budget.

    Intended for tests and debugging. Production workers use
    ``ProcessAnalysisExecutor`` so that runaway work can be stopped.
    """

    def __init__(self, analyze: AnalysisFunction = run_registered_analysis) -> None:
        self._analyze = analyze

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisOutcome:
        """Run the analyzer and translate failures into executor errors."""
        try:
            return self._analyze(analyzer, language, text)
        except Exception as err:  # analyzer failures are final, never retried
            raise AnalysisRejectedError(
                error_code_for(err), "The analysis failed."
            ) from err


class ProcessAnalysisExecutor:
    """Run each analysis in a child process that is killed when over budget.

    The wall-clock budget includes child start-up. On POSIX the child also sets
    ``RLIMIT_CPU`` as a backstop and ``RLIMIT_AS`` for the memory budget where
    the operating system supports it.
    """

    def __init__(self, analyze: AnalysisFunction = run_registered_analysis) -> None:
        self._analyze = analyze
        # Spawn avoids forking a process that hosts relay and worker threads.
        self._context: SpawnContext = multiprocessing.get_context("spawn")

    def execute(
        self, analyzer: str, language: str, text: str, budget: ExecutionBudget
    ) -> AnalysisOutcome:
        """Run the analyzer in a child process and wait at most the time budget."""
        seconds = budget.time_limit.total_seconds()
        receiver, sender = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_child_main,
            args=(
                sender,
                self._analyze,
                analyzer,
                language,
                text,
                seconds,
                budget.memory_limit_bytes,
            ),
            daemon=True,
        )
        process.start()
        sender.close()
        try:
            if not receiver.poll(seconds):
                raise AnalysisBudgetExceededError(
                    TIME_BUDGET_EXCEEDED, "The analysis exceeded its time budget."
                )
            try:
                kind, payload = receiver.recv()
            except EOFError:
                kind, payload = None, None
        finally:
            if process.is_alive():
                process.kill()
            process.join()
            receiver.close()

        if kind == "ok":
            return payload  # type: ignore[no-any-return]
        if kind == "budget":
            raise AnalysisBudgetExceededError(
                str(payload), "The analysis exceeded its budget."
            )
        if kind == "rejected":
            raise AnalysisRejectedError(str(payload), "The analysis failed.")
        if _SIGXCPU is not None and process.exitcode == -_SIGXCPU:
            raise AnalysisBudgetExceededError(
                TIME_BUDGET_EXCEEDED, "The analysis exceeded its CPU budget."
            )
        raise AnalysisInterruptedError(
            ANALYSIS_INTERRUPTED,
            f"The analysis process exited unexpectedly ({process.exitcode}).",
        )


def _apply_limits(
    cpu_seconds: float, memory_limit_bytes: int | None
) -> None:  # pragma: no cover - runs in a child process
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX platforms
        return
    cpu_limit = math.ceil(cpu_seconds) + 1
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
    except (ValueError, OSError):  # pragma: no cover - platform dependent
        pass
    if memory_limit_bytes is not None:
        try:
            resource.setrlimit(
                resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes)
            )
        except (ValueError, OSError):  # pragma: no cover - unsupported on macOS
            pass


def _child_main(
    connection: Connection,
    analyze: AnalysisFunction,
    analyzer: str,
    language: str,
    text: str,
    cpu_seconds: float,
    memory_limit_bytes: int | None,
) -> None:  # pragma: no cover - runs in a child process
    result: tuple[str, Any]
    try:
        _apply_limits(cpu_seconds, memory_limit_bytes)
        result = ("ok", analyze(analyzer, language, text))
    except MemoryError:
        result = ("budget", MEMORY_BUDGET_EXCEEDED)
    except Exception as err:  # child boundary: report a code, never the input
        result = ("rejected", error_code_for(err))
    try:
        connection.send(result)
    except MemoryError:
        connection.send(("budget", MEMORY_BUDGET_EXCEEDED))
    finally:
        connection.close()
