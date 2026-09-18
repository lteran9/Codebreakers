# Phase 3 - Cipher Catalog and Cryptanalysis

## Milestone

`Phase 3 - Cipher Catalog and Cryptanalysis`

Branch: `phase-3-cipher-toolkit`

Labels:

- `phase:3`
- `type:feature`
- `type:testing`
- `type:benchmark`
- `type:docs`
- `area:ciphers`
- `area:cryptanalysis`
- `area:testing`

## Epic: Phase 3 - Cipher Catalog and Cryptanalysis

Labels: `epic`, `phase:3`
Milestone: `Phase 3 - Cipher Catalog and Cryptanalysis`

### Summary

Expand the domain with representative cipher families and introduce separate cryptanalysis capabilities that can rank candidate solutions without requiring the encryption key.

### Outcome

The project supports substitution, Vigenere, and homophonic cipher models, an explicit cipher registry, reusable text statistics, and selected cryptanalysis workflows with deterministic tests and documented limitations.

### Child Issues

- [x] Implement monoalphabetic substitution
- [x] Implement Vigenere
- [x] Implement homophonic substitution
- [x] Create the explicit cipher registry
- [x] Add versioned historical fixtures
- [x] Define cryptanalysis protocols and candidate models
- [x] Implement reusable text statistics
- [x] Implement Caesar brute-force analysis
- [x] Add substitution frequency-analysis assistance
- [x] Implement Vigenere key-length estimation and candidate ranking
- [x] Add optional homophonic analysis reports
- [x] Add cipher round-trip and malformed-key tests
- [x] Add solver corpus and ranking tests
- [x] Add deterministic benchmark scripts and quality targets

### Definition of Done

- [x] New cipher implementations require no changes to existing cipher classes.
- [x] Decryption is deterministic for every cipher.
- [x] Homophonic encryption is reproducible with a fixed random seed.
- [x] Analyzers return ranked candidates or explainable reports.
- [x] Algorithm limitations and minimum useful ciphertext lengths are documented.
- [x] Unit, property, solver, and benchmark checks pass locally and in CI.

## Issue 1: Implement monoalphabetic substitution

Labels: `type:feature`, `area:ciphers`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Implement monoalphabetic substitution using the existing cipher abstractions.
- [x] Validate that the substitution key is bijective.
- [x] Define behavior for malformed, duplicate, incomplete, and ambiguous keys.
- [x] Preserve the established text normalization and symbol-handling policies.

### Acceptance Criteria

- [x] Valid bijective keys encrypt and decrypt supported text.
- [x] Duplicate, incomplete, or ambiguous keys raise intentional domain errors.
- [x] The implementation has no framework or I/O dependencies.
- [x] Existing cipher implementations remain unchanged.

## Issue 2: Implement Vigenere

Labels: `type:feature`, `area:ciphers`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Implement Vigenere using the existing cipher abstractions.
- [x] Validate the repeating keyword.
- [x] Define how the key advances across non-alphabet symbols.
- [x] Preserve the established Unicode normalization and symbol-handling policies.

### Acceptance Criteria

- [x] Valid keywords encrypt and decrypt supported text.
- [x] Invalid or empty keywords raise intentional domain errors.
- [x] Key advancement behavior for non-alphabet symbols is explicit and tested.
- [x] The implementation is fully typed and framework-free.

## Issue 3: Implement homophonic substitution

Labels: `type:feature`, `area:ciphers`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Define the homophonic substitution model.
- [x] Validate that ciphertext tokens are unique and mappings are valid.
- [x] Inject a seeded random source for encryption.
- [x] Keep decryption deterministic.
- [x] Define behavior for malformed, duplicate, incomplete, and ambiguous mappings.

### Acceptance Criteria

- [x] Valid mappings encrypt and decrypt supported input.
- [x] Encryption is non-deterministic by default.
- [x] Encryption is reproducible with a fixed random seed.
- [x] Decryption is deterministic.
- [x] Invalid mappings raise intentional domain errors.

## Issue 4: Create the explicit cipher registry

Labels: `type:feature`, `area:application`, `area:architecture`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Extend the shared composition module with the supported cipher registry.
- [x] Register substitution, Vigenere, and homophonic cipher implementations.
- [x] Reuse the registry from the existing CLI wiring.
- [x] Keep registry construction explicit and typed.

### Acceptance Criteria

- [x] Supported ciphers can be selected through the registry.
- [x] Unknown cipher names produce an intentional error.
- [x] Adding a cipher does not require modifying existing cipher classes.
- [x] The registry does not use runtime subclass discovery or import side effects.

## Issue 5: Add versioned historical fixtures

Labels: `type:docs`, `type:testing`, `area:ciphers`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Store historical examples and expected results as versioned fixtures.
- [x] Include provenance for each fixture.
- [x] Include licensing notes for the source material.
- [x] Use fixtures in relevant cipher and solver tests.

### Acceptance Criteria

- [x] Fixtures are reproducible and committed to the repository.
- [x] Each fixture identifies its provenance.
- [x] Licensing information is recorded.
- [x] Tests can load the fixtures without network access.

## Issue 6: Define cryptanalysis protocols and candidate models

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Create `src/codebreakers/domain/cryptanalysis/`.
- [x] Define typed `Analyzer` protocols.
- [x] Define ranked candidate result models.
- [x] Include explainable scores in candidate results.
- [x] Keep cryptanalysis independent from concrete cipher implementations where possible.

### Acceptance Criteria

- [x] Analyzer protocols are separate from the `Cipher` protocol.
- [x] Candidate results contain a rankable score and explainable result data.
- [x] The cryptanalysis package has no framework dependencies.
- [x] Types pass the repository's strict type-checking rules.

## Issue 7: Implement reusable text statistics

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Add symbol-count calculations.
- [x] Add n-gram-count calculations.
- [x] Add index-of-coincidence calculations.
- [x] Add configurable language scoring.
- [x] Define behavior for empty and insufficient input.

### Acceptance Criteria

- [x] Statistics are reusable by multiple analyzers.
- [x] Results are deterministic for the same input and configuration.
- [x] Empty and insufficient input produce intentional results or domain errors.
- [x] Language configuration is explicit.

## Issue 8: Implement Caesar brute-force analysis

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Implement Caesar brute-force analysis.
- [x] Generate a candidate for every possible shift.
- [x] Score candidates using the configurable language-scoring capability.
- [x] Return ranked candidates with explainable scores.
- [x] Make the selected language explicit in analysis requests.

### Acceptance Criteria

- [x] Every valid Caesar shift is represented in the result.
- [x] Candidates are ordered by score.
- [x] Scores and selected language are available to the caller.
- [x] The correct key can be placed in the top candidate for the documented corpus and quality target.

## Issue 9: Add substitution frequency-analysis assistance

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Add frequency-analysis assistance for substitution ciphers.
- [x] Report symbol distributions.
- [x] Compare distributions against the selected language model or frequency data.
- [x] Return explainable analysis results.
- [x] Document that this milestone provides assistance rather than a fully automated solver.

### Acceptance Criteria

- [x] Frequency reports are deterministic.
- [x] The selected language is explicit.
- [x] Results expose the measurements used to produce the report.
- [x] The feature does not claim to fully solve arbitrary substitution ciphertext.

## Issue 10: Implement Vigenere key-length estimation and candidate ranking

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Implement key-length estimation using index of coincidence or Kasiski examination.
- [x] Rank candidate key lengths.
- [x] Rank candidate keys for suitable ciphertext.
- [x] Make language models or frequency data versioned.
- [x] Make the selected language explicit in analysis requests.

### Acceptance Criteria

- [x] Candidate key lengths are returned in ranked order.
- [x] Candidate keys are returned with explainable scores.
- [x] Language model or frequency-data versions are identifiable.
- [x] Minimum useful ciphertext lengths and limitations are documented.

## Issue 11: Add optional homophonic analysis reports

Labels: `type:feature`, `area:cryptanalysis`, `phase:3`, `optional`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Add symbol-distribution reports for homophonic ciphertext.
- [x] Add frequency-analysis reports where meaningful.
- [x] Document automated homophonic solving as an optional advanced milestone.
- [x] Do not introduce automated solving unless its scope and limitations are explicitly defined.

### Acceptance Criteria

- [x] Reports are deterministic.
- [x] Reports expose the measured symbol distributions.
- [x] The feature does not claim to automatically solve homophonic ciphertext.
- [x] Automated homophonic solving remains optional.

## Issue 12: Add cipher round-trip and malformed-key tests

Labels: `type:testing`, `area:testing`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Add unit tests for substitution round trips.
- [x] Add unit tests for Vigenere round trips.
- [x] Add unit tests for homophonic encryption and deterministic decryption.
- [x] Seed the random source for reproducible homophonic encryption tests.
- [x] Test malformed, duplicate, incomplete, and ambiguous keys.
- [x] Test the round-trip invariant over normalized input.

### Acceptance Criteria

- [x] Every cipher has round-trip coverage for supported input.
- [x] Homophonic tests are reproducible under a fixed seed.
- [x] Invalid keys are rejected consistently.
- [x] Tests cover the established normalization and symbol policies.

## Issue 13: Add solver corpus and ranking tests

Labels: `type:testing`, `area:cryptanalysis`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Create a corpus of known cipher and plaintext examples.
- [x] Include the expected key or candidate where applicable.
- [x] Test Caesar solver ranking quality.
- [x] Test Vigenere candidate-key ranking for sufficient ciphertext.
- [x] Test frequency-analysis output against known distributions.

### Acceptance Criteria

- [x] Corpus fixtures are versioned and traceable.
- [x] Solver tests are deterministic.
- [x] Ranking quality is measured against documented expectations.
- [x] Tests identify when a correct candidate falls below the target rank.

## Issue 14: Add deterministic benchmarks and quality targets

Labels: `type:benchmark`, `type:docs`, `area:testing`, `phase:3`
Branch: `phase-3-cipher-toolkit`

### Tasks

- [x] Add deterministic benchmark scripts for increasing ciphertext sizes.
- [x] Record input size.
- [x] Record runtime.
- [x] Record Python version.
- [x] Record machine details.
- [x] Define quality targets for Caesar candidate ranking.
- [x] Define quality targets for recovering known Vigenere keys from sufficient ciphertext.
- [x] Document algorithm limitations and minimum useful ciphertext lengths.

### Acceptance Criteria

- [x] Benchmarks can be rerun with documented commands.
- [x] Benchmark output records the required environment details.
- [x] Quality targets are explicit and reproducible.
- [x] Published results distinguish measurements from expectations.
- [x] Limitations and minimum useful input lengths are documented.

## Phase Exit Checklist

- [x] Monoalphabetic substitution is implemented with validated bijective keys.
- [x] Vigenere is implemented with explicit key-advance behavior.
- [x] Homophonic substitution is reproducible with a fixed seed and deterministic during decryption.
- [x] The explicit cipher registry is used by the shared composition module.
- [x] Historical fixtures include provenance and licensing notes.
- [x] Cryptanalysis protocols and ranked candidate models are defined.
- [x] Text statistics are reusable and configurable.
- [x] Caesar brute-force analysis returns ranked candidates.
- [x] Substitution frequency-analysis assistance is available.
- [x] Vigenere key-length estimation and candidate ranking are available.
- [x] Homophonic analysis reports are available, with automated solving remaining optional.
- [x] Cipher round-trip, malformed-key, corpus, and ranking tests pass.
- [x] Deterministic benchmarks and documented quality targets are available.
