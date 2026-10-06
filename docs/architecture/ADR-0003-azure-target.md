# ADR 0003: Initial Azure Target

- Status: Accepted
- Date: 2026-09-13

## Context

The project roadmap includes a future cloud deployment story, but the early focus is on a clean local baseline. A target hosting model helps frame the later infrastructure work without forcing it prematurely.

## Decision

The initial production target will be Azure Container Apps with PostgreSQL Flexible Server, Azure Service Bus, Key Vault, and Azure Monitor. This keeps the architecture aligned with a single deployable service and a later job-processing worker model.

## Consequences

- Infra choices are consistent with the roadmap and future phases.
- The project explicitly treats Azure as a deployment target rather than a domain dependency.
- Local development remains independent and can be run without cloud dependencies.

## Amendment (Phase 8)

[ADR-0012](ADR-0012-azure-dev-environment.md) deploys this target with
Terraform ([ADR-0011](ADR-0011-terraform.md)) but defers Azure Service Bus from
the first cloud environment. The `dev` environment uses the PostgreSQL queue
instead. The Service Bus adapter and its path back remain documented and
tested.
