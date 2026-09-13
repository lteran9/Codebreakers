# ADR 0004: Unicode and Text Handling Policy

- Status: Accepted
- Date: 2026-09-13

## Context

Cipher operations work over text and must be stable across common Unicode input, especially historical documents that may contain accented letters, ligatures, or non-Latin characters. Without a shared policy, domain behavior becomes inconsistent and hard to test.

## Decision

We will normalize text at the domain boundary using NFC before any cipher processing. Case-preservation policy will be explicit per cipher and mirrored in tests rather than treated as implicit behavior. Character indexing operates on Unicode code points, not grapheme clusters, because the initial ciphers operate on symbol sets and alphabets rather than user-perceived display characters. Historical digits and non-English symbols are treated as non-alphabet symbols unless a specific cipher explicitly defines them as supportable characters.

## Consequences

- Composed and decomposed forms of the same character are treated consistently.
- Cipher behavior remains deterministic and predictable for supported alphabets.
- Unsupported symbols are handled intentionally instead of silently mutating the input.
