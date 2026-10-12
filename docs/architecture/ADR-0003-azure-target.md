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

## Alternatives considered

Recorded in Phase 10.

| Alternative | Why not |
| --- | --- |
| Azure Kubernetes Service | Gives full control, but the cluster, node pools, ingress, and upgrades are a standing operational and cost burden for three small processes. Container Apps runs KEDA and Envoy for us. |
| Azure App Service | Suits the API but has no first-class scale-to-zero or event-driven scaling for background workers. The worker would need a separate product. |
| Azure Functions | The analyzer runs in a budgeted child process for up to 30 s, and the project ships one container image. A Functions host would change the programming model for the worker only. |
| Cosmos DB or Azure SQL | See [ADR-0008](ADR-0008-analysis-persistence.md). PostgreSQL also serves as the local queue substitute ([ADR-0010](ADR-0010-containers-local-stack.md)). |

## Amendment (Phase 8)

[ADR-0012](ADR-0012-azure-dev-environment.md) deploys this target with
Terraform ([ADR-0011](ADR-0011-terraform.md)) but defers Azure Service Bus from
the first cloud environment. The `dev` environment uses the PostgreSQL queue
instead. The Service Bus adapter and its path back remain documented and
tested.
