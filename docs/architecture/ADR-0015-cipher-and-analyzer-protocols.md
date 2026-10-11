# ADR-0015: Cipher and Analyzer Protocols

## Status

Accepted. Recorded in Phase 10 to document a decision made across Phases 1–3.
Builds on [ADR-0002](ADR-0002-clean-architecture.md) and
[ADR-0005](ADR-0005-stateless-cipher-keys.md).

## Context

The project needs a cipher catalogue (Caesar, monoalphabetic substitution,
Vigenère, homophonic substitution) and a separate set of cryptanalysis tools.
Both are reached from the CLI, the HTTP API, and the worker. Each cipher has a
different key: an integer shift, a keyword, a full permutation, or a
one-to-many token mapping. Adding a cipher or analyzer must not require
changes to existing ones or to the adapters.

## Decision

- **Structural protocols, not base classes.** `Cipher[KeyT]`
  (`domain/protocols.py`) declares `encrypt` and `decrypt`, both taking the
  text, a typed key, and optional `TransformOptions`. `Analyzer[ResultT]`
  (`domain/cryptanalysis/models.py`) declares a single `analyze(text)`. Concrete
  classes do not inherit from anything. `mypy --strict` checks conformance
  wherever they are used.
- **Encryption and analysis are separate.** Following interface segregation,
  a cipher cannot be asked to break itself, and an analyzer does not need a
  key. Analyzers compose ciphers (the Caesar brute-force analyzer decrypts with
  `CaesarCipher`), not the reverse.
- **Typed key value objects.** Keys are parsed once at the boundary into
  immutable, validated objects: `int`, `VigenereKey`, `SubstitutionKey`, and
  `HomophonicKey`. Invalid keys fail with domain errors such as
  `InvalidKeyError` or `IncompleteKeyError` before any text is transformed.
- **Shared text pipeline.** NFC normalization, case strategy, and unknown-symbol
  strategy live in `domain/text.py` and `TransformOptions`
  ([ADR-0004](ADR-0004-text-handling.md),
  [ADR-0006](ADR-0006-case-and-symbol-strategy.md)). Each cipher supplies only
  its index transformation.
- **Explicit registries at the composition root.** `composition.py` maps
  names to a `CipherSpec` (cipher, key parser, key help text) and to analyzer
  factories that take a `LanguageModel`. The CLI and API both resolve names
  through these registries, so they cannot drift apart.
- **Injected randomness.** The homophonic cipher takes a `random.Random`, so
  tests and benchmarks are deterministic.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Abstract base classes (`abc.ABC`) | Nominal inheritance couples every cipher to a base module and invites shared mutable state in the base. Protocols give the same static checking without the coupling. |
| One `Cipher` interface with an `analyze` method | Most analysis does not map one-to-one onto a cipher. Homophonic analysis only reports token distributions. Forcing it into the cipher interface would produce stub methods. |
| Raw `str` keys parsed inside each cipher | Validation would repeat in every operation, and errors would surface mid-transform. Typed keys fail fast and keep `encrypt` and `decrypt` free of parsing. |
| Plugin discovery through entry points | Four ciphers and four analyzers do not justify dynamic loading. An explicit dictionary is easier to read, test, and type-check. |
| Ciphers constructed with their key (`CaesarCipher(3)`) | Rejected in ADR-0005. Stateless instances can be shared across requests and threads. |

## Consequences

- A new cipher is one domain class, one key parser, and one registry entry.
  The CLI, API, and OpenAPI document pick it up without other changes.
- Generic protocols need care with variance. The key type parameter is
  contravariant and the result type covariant, which mypy enforces.
- Registries are module-level constants. Swapping language models or
  analyzers at runtime would need a factory, which no current feature
  requires.
