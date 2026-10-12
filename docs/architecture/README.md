# Architecture Decision Records

Each ADR records one consequential decision: its context, what was decided,
the alternatives, and the consequences. Later ADRs amend earlier ones instead
of rewriting them, so the history of a decision stays readable.

## Where to find a decision

| Topic | ADR |
| --- | --- |
| Domain boundaries and dependency direction | [ADR-0002](ADR-0002-clean-architecture.md) |
| Cipher and analyzer protocols, typed keys, registries | [ADR-0015](ADR-0015-cipher-and-analyzer-protocols.md), [ADR-0005](ADR-0005-stateless-cipher-keys.md) |
| Text normalization, case, and unknown symbols | [ADR-0004](ADR-0004-text-handling.md), [ADR-0006](ADR-0006-case-and-symbol-strategy.md) |
| HTTP contract, errors, and limits | [ADR-0007](ADR-0007-http-api-contract.md) |
| Analysis job state machine | [ADR-0016](ADR-0016-analysis-job-state-machine.md) |
| PostgreSQL persistence and retention | [ADR-0008](ADR-0008-analysis-persistence.md) |
| Outbox, worker, and Service Bus | [ADR-0009](ADR-0009-async-analysis-worker.md) |
| Container image and local stack (PostgreSQL queue) | [ADR-0010](ADR-0010-containers-local-stack.md) |
| Azure Container Apps as the target | [ADR-0003](ADR-0003-azure-target.md), [ADR-0012](ADR-0012-azure-dev-environment.md) |
| Terraform and state | [ADR-0011](ADR-0011-terraform.md) |
| Managed identity and OIDC | [ADR-0017](ADR-0017-workload-and-delivery-identity.md) |
| Continuous delivery, staged rollout, rollback | [ADR-0013](ADR-0013-continuous-delivery.md) |
| Logs, traces, metrics, and alerts | [ADR-0014](ADR-0014-observability.md) |

## All ADRs

| ADR | Title | Phase |
| --- | --- | --- |
| [0001](ADR-0001-source-layout.md) | Source layout | 0 |
| [0002](ADR-0002-clean-architecture.md) | Clean Architecture boundary | 0 |
| [0003](ADR-0003-azure-target.md) | Initial Azure target | 0 (amended 8) |
| [0004](ADR-0004-text-handling.md) | Unicode and text handling policy | 0 |
| [0005](ADR-0005-stateless-cipher-keys.md) | Stateless cipher key passing | 1 |
| [0006](ADR-0006-case-and-symbol-strategy.md) | Case and unknown symbol strategy | 1 |
| [0007](ADR-0007-http-api-contract.md) | HTTP API contract | 4 |
| [0008](ADR-0008-analysis-persistence.md) | Durable analysis jobs | 5 |
| [0009](ADR-0009-async-analysis-worker.md) | Asynchronous analysis worker | 6 |
| [0010](ADR-0010-containers-local-stack.md) | Container image and local production stack | 7 |
| [0011](ADR-0011-terraform.md) | Terraform for Azure infrastructure | 8 |
| [0012](ADR-0012-azure-dev-environment.md) | Azure development environment | 8 |
| [0013](ADR-0013-continuous-delivery.md) | Continuous delivery to Azure Container Apps | 9 |
| [0014](ADR-0014-observability.md) | Observability | 9 |
| [0015](ADR-0015-cipher-and-analyzer-protocols.md) | Cipher and analyzer protocols | 10 (records 1–3) |
| [0016](ADR-0016-analysis-job-state-machine.md) | Analysis job state machine | 10 (records 5–6) |
| [0017](ADR-0017-workload-and-delivery-identity.md) | Workload and delivery identity | 10 (records 8–9) |
