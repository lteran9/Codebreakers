# ADR 0006: Case and Unknown Symbol Strategy

- Status: Accepted
- Date: 2026-09-15

## Context

Encrypted historical communications contain punctuation, numbers, spaces, and mixed casing. Ciphers operating over a fixed alphabet (such as Latin A-Z) need deterministic rules for handling characters outside the alphabet and managing casing.

## Decision

We will provide configurable strategies via immutable `TransformOptions`:
- `CaseStrategy`:
  - `PRESERVE` (default): Encrypts or decrypts letters based on alphabet indices while preserving the original character's uppercase or lowercase form.
  - `UPPERCASE`: Forces transformed alphabet characters to uppercase.
  - `LOWERCASE`: Forces transformed alphabet characters to lowercase.
  - `IGNORE`: Returns the raw alphabet symbol as defined without adjusting case.
- `UnknownSymbolStrategy`:
  - `PASS_THROUGH` (default): Leaves symbols not matching the alphabet unchanged in output (e.g. spaces, commas).
  - `STRIP`: Omits non-alphabet symbols from output.
  - `REJECT`: Raises an `UnknownSymbolError` when an unmapped symbol is encountered.

## Consequences

- Flexible for both historical text processing (pass-through punctuation) and strict cryptanalysis benchmarks (strip non-alphabet characters or reject invalid inputs).
- Default behavior preserves user formatting and character case without losing non-alphabet characters.
