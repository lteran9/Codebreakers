"""Deterministic cryptanalysis and cipher benchmarks.

Measures the median runtime of every registered analyzer, and of cipher
encrypt/decrypt round trips, at fixed input sizes up to the API text cap.
It also checks that the Caesar and Vigenere analyzers recover the key, so a
speed-up that breaks recovery is visible. Output is JSON:

    python scripts/benchmark.py
    python scripts/benchmark.py --sizes 128 2048 20000 --iterations 5
"""

import argparse
import json
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from random import Random

from codebreakers.composition import ANALYZER_REGISTRY
from codebreakers.domain.ciphers.caesar import CaesarCipher
from codebreakers.domain.ciphers.homophonic import (
    HomophonicKey,
    HomophonicSubstitutionCipher,
)
from codebreakers.domain.ciphers.substitution import (
    SubstitutionCipher,
    SubstitutionKey,
)
from codebreakers.domain.ciphers.vigenere import VigenereCipher, VigenereKey
from codebreakers.domain.cryptanalysis.analyzers import (
    CaesarBruteForceAnalyzer,
    VigenereAnalyzer,
)
from codebreakers.domain.cryptanalysis.statistics import ENGLISH_LANGUAGE_MODEL
from codebreakers.domain.models import Alphabet, CaseStrategy, TransformOptions
from codebreakers.domain.protocols import Cipher

# Original prose, so the letter distribution resembles ordinary English text.
CORPUS = (
    "THE KEEPER OF THE LIGHTHOUSE WROTE IN HIS LOG EVERY EVENING. HE NOTED "
    "THE WIND, THE HEIGHT OF THE WAVES, AND THE NAMES OF THE SHIPS THAT "
    "PASSED THE POINT BEFORE DARK. MOST NIGHTS WERE QUIET, BUT IN WINTER THE "
    "STORMS CAME FROM THE NORTH WITHOUT WARNING, AND HE WOULD CLIMB THE "
    "STAIRS TO TRIM THE LAMP UNTIL MORNING. "
)
CAESAR_SHIFT = 3
VIGENERE_KEYWORD = "LANTERN"
SUBSTITUTION_ALPHABET = "QWERTYUIOPASDFGHJKLZXCVBNM"
DEFAULT_SIZES = (128, 2_048, 20_000)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Median runtime of one benchmark at one input size."""

    benchmark: str
    input_size: int
    median_seconds: float
    iterations: int
    key_recovered: bool | None


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    """Benchmark results together with the environment that produced them."""

    python_version: str
    platform: str
    machine: str
    results: tuple[BenchmarkResult, ...]


def _plaintext(size: int) -> str:
    return (CORPUS * (size // len(CORPUS) + 1))[:size]


def _median_runtime(operation: Callable[[], object], iterations: int) -> float:
    timings: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        operation()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)


def _homophonic_key(alphabet: Alphabet) -> HomophonicKey:
    return HomophonicKey.from_mapping(
        alphabet,
        {
            symbol: [f"{index:02d}", f"{index + 26:02d}"]
            for index, symbol in enumerate(alphabet.symbols)
        },
    )


def _benchmarks_for_size(
    size: int,
) -> tuple[dict[str, Callable[[], object]], dict[str, bool]]:
    alphabet = Alphabet.standard_latin()
    options = TransformOptions(case_strategy=CaseStrategy.UPPERCASE)
    vigenere_key = VigenereKey.from_keyword(VIGENERE_KEYWORD, options)
    substitution_key = SubstitutionKey.from_cipher_alphabet(
        alphabet, SUBSTITUTION_ALPHABET
    )
    homophonic_key = _homophonic_key(alphabet)
    caesar, vigenere = CaesarCipher(), VigenereCipher()
    substitution = SubstitutionCipher()
    homophonic = HomophonicSubstitutionCipher(random_source=Random(7))

    plaintext = _plaintext(size)
    caesar_text = caesar.encrypt(plaintext, CAESAR_SHIFT, options)
    vigenere_text = vigenere.encrypt(plaintext, vigenere_key, options)
    substitution_text = substitution.encrypt(plaintext, substitution_key, options)
    homophonic_text = homophonic.encrypt(plaintext, homophonic_key, options)

    recovered = {
        "caesar-bruteforce": CaesarBruteForceAnalyzer()
        .analyze(caesar_text)
        .candidates[0]
        .key
        == str(CAESAR_SHIFT),
        "vigenere-frequency": VigenereAnalyzer()
        .analyze(vigenere_text)
        .candidate_keys[0]
        .key
        == VIGENERE_KEYWORD,
    }

    def round_trip[KeyT](cipher: Cipher[KeyT], key: KeyT) -> Callable[[], object]:
        return lambda: cipher.decrypt(
            cipher.encrypt(plaintext, key, options), key, options
        )

    analyzer_inputs = {
        "caesar-bruteforce": caesar_text,
        "vigenere-frequency": vigenere_text,
        "substitution-frequency": substitution_text,
        "homophonic-distribution": homophonic_text,
    }
    operations: dict[str, Callable[[], object]] = {
        name: lambda factory=factory, name=name: factory(
            ENGLISH_LANGUAGE_MODEL
        ).analyze(analyzer_inputs.get(name, caesar_text))
        for name, factory in ANALYZER_REGISTRY.items()
    }
    operations.update(
        {
            "caesar-round-trip": round_trip(caesar, CAESAR_SHIFT),
            "vigenere-round-trip": round_trip(vigenere, vigenere_key),
            "substitution-round-trip": round_trip(substitution, substitution_key),
            "homophonic-round-trip": round_trip(homophonic, homophonic_key),
        }
    )
    return operations, recovered


def run_benchmarks(sizes: tuple[int, ...], iterations: int) -> BenchmarkRun:
    """Run every benchmark at every size."""
    results: list[BenchmarkResult] = []
    for size in sizes:
        operations, recovered = _benchmarks_for_size(size)
        results.extend(
            BenchmarkResult(
                benchmark=name,
                input_size=size,
                median_seconds=round(_median_runtime(operation, iterations), 6),
                iterations=iterations,
                key_recovered=recovered.get(name),
            )
            for name, operation in operations.items()
        )
    return BenchmarkRun(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        machine=platform.machine(),
        results=tuple(results),
    )


def main(argv: list[str] | None = None) -> None:
    """Run the benchmarks and print JSON output."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args(argv)
    run = run_benchmarks(tuple(args.sizes), args.iterations)
    print(json.dumps(asdict(run), indent=2))


if __name__ == "__main__":
    main()
