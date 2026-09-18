"""Deterministic Phase 3 cryptanalysis benchmark script."""

import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass

from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.cryptanalysis.analyzers import CaesarBruteForceAnalyzer


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Single benchmark measurement."""

    benchmark: str
    input_size: int
    runtime_seconds: float
    python_version: str
    machine: str
    processor: str
    iterations: int


def run_caesar_benchmark(input_size: int, iterations: int = 5) -> BenchmarkResult:
    """Measure Caesar brute-force analysis for a deterministic ciphertext size."""
    sample = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG "
    plaintext = (sample * (input_size // len(sample) + 1))[:input_size]
    ciphertext = CaesarCipher().encrypt(plaintext, 3)
    analyzer = CaesarBruteForceAnalyzer()
    timings: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        analyzer.analyze(ciphertext)
        timings.append(time.perf_counter() - start)
    return BenchmarkResult(
        benchmark="caesar-bruteforce",
        input_size=input_size,
        runtime_seconds=statistics.median(timings),
        python_version=sys.version.split()[0],
        machine=platform.machine(),
        processor=platform.processor(),
        iterations=iterations,
    )


def main() -> None:
    """Run deterministic Phase 3 benchmarks and print JSON output."""
    results = [run_caesar_benchmark(size) for size in (128, 512, 2048)]
    print(json.dumps([asdict(result) for result in results], indent=2))


if __name__ == "__main__":
    main()
