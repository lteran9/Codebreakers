# ADR 0005: Stateless Cipher Key Passing

- Status: Accepted
- Date: 2026-09-15

## Context

Cipher implementations need to be invoked across different boundaries (CLI commands, HTTP requests, cryptanalysis pipelines, and worker jobs). We had to decide whether keys should be passed per operation to stateless cipher instances (`encrypt(text, key)`) or bound at cipher construction (`CaesarCipher(shift=3).encrypt(text)`).

## Decision

We will design cipher implementations as stateless objects adhering to `Cipher[KeyT]`, passing keys as arguments to `encrypt` and `decrypt`.

## Consequences

- Cipher instances can be registered and reused as singletons in the Phase 3 cipher registry without lifecycle management.
- CLI, API, and batch workflows can execute operations across multiple keys without instantiating new cipher instances for each request.
- The `Cipher[KeyT]` protocol remains generic over `KeyT`, ensuring strict type checking for distinct key types (e.g., `int` for Caesar, `str` for Vigenere).
