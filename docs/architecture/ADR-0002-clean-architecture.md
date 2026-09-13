# ADR 0002: Clean Architecture Boundary

- Status: Accepted
- Date: 2026-09-13

## Context

The roadmap anticipates multiple interfaces for the same cipher logic, including CLI, HTTP, and later queue-driven workers. The business domain should not depend on a specific framework or infrastructure tool.

## Decision

We will organize the project with dependency flow pointing inward: adapters depend on application services, and domain logic remains independent from I/O, network, and persistence. The domain layer owns cipher behavior, value objects, and error definitions.

## Consequences

- Domain modules remain easy to test and reason about.
- Framework changes can happen without rewriting cipher rules.
- Application services act as the explicit boundary for orchestration and policy.
