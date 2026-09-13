# ADR 0001: Source Layout

- Status: Accepted
- Date: 2026-09-13

## Context

The project starts small, but the roadmap makes a clean architecture explicit from the beginning. Keeping core cipher logic separate from adapters reduces framework coupling and makes the package easier to test and extend.

## Decision

We will use a src-layout package in `src/codebreakers` with domain, application, and adapter packages separated by responsibility. Tests stay under `tests/` and remain independent from production dependencies.

## Consequences

- Production code is imported from `src` and can be installed consistently.
- Domain behavior remains portable and framework-agnostic.
- Future CLI, API, and infrastructure modules can be added without changing core cipher implementations.
