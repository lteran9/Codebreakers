# ADR-0011: Terraform for Azure Infrastructure

## Status

Accepted.

## Context

Phase 8 provisions the Azure environment that hosts the API, relay, worker,
database, secrets, and telemetry ([ADR-0012](ADR-0012-azure-dev-environment.md)).
The environment must be reproducible from code, reviewable before it changes,
and destroyable without portal-only steps. Separate `dev` and `prod`
environments must share one definition while keeping their state isolated.

## Decision

### Terraform over Bicep and Pulumi

Terraform (HCL, `hashicorp/azurerm`) defines the infrastructure.

- **Bicep** is Azure-native and needs no state file. It has no plan file that
  can be saved and applied exactly as reviewed, and no mock-provider test
  framework. Its skills also do not transfer beyond Azure.
- **Pulumi** would allow Python. However, it adds a second programming model
  next to the application code and a hosted or self-managed state service.
- **Terraform** offers saved plans, `terraform test` with mock providers,
  mature Checkov coverage, and the widest industry use.

### Layout

```text
infra/
  bootstrap/            one-time state storage (local state)
  azure/                the environment definition
    environments/       <env>.tfvars inputs and <env>.backend.hcl state settings
    tests/              terraform test suite with mock providers
```

One root module with `dev.tfvars` and `prod.tfvars` keeps the environments
identical except for sizing, replicas, budgets, and protection flags. There
are no child modules. Each concern has its own file (`database.tf`,
`container_apps.tf`, and so on), and the three apps and two jobs share one
`for_each` definition. Modules would add indirection without reuse.

### Remote state

- **Bootstrap.** `infra/bootstrap` creates `rg-codebreakers-tfstate` and a
  StorageV2 account with GRS, TLS 1.2, infrastructure encryption, blob
  versioning, 30-day soft delete, and a `CanNotDelete` lock. It cannot store
  its own state before the account exists, so its small local state holds
  only identifiers and is git-ignored. Losing that state does not affect any
  environment, because the resources can be imported again.
- **Per-environment isolation.** Each environment has its own blob container,
  `tfstate-dev` or `tfstate-prod`, with key `codebreakers.tfstate`. A later
  CI identity (Phase 9) can then be granted access to `dev` state only.
- **Locking.** The `azurerm` backend takes a blob lease for every state
  operation, so concurrent applies fail instead of corrupting state.
- **Access.** Shared keys and SAS tokens are disabled
  (`shared_access_key_enabled = false`). The backend uses Microsoft Entra
  authentication (`use_azuread_auth = true`), and the bootstrap grants the
  deployer *Storage Blob Data Contributor* on the account.
- **Sensitive values.** The generated PostgreSQL password exists in state
  (ADR-0012). State is therefore treated as a secret: Entra-only access, no
  local copies of environment state, and no secret outputs.

### Version strategy

- **Terraform CLI** `>= 1.16, < 2.0`. CI pins `1.16.5`.
- **Providers** use pessimistic constraints: `azurerm ~> 5.8`,
  `random ~> 3.9`, and `time ~> 0.14`. Minor releases are allowed and major
  releases need a deliberate change.
- **Lock files.** `.terraform.lock.hcl` is committed for each stack, with
  hashes for Linux, macOS, and Windows on amd64 and arm64, so every machine
  and CI install the same provider builds.
- **Updates.** Dependabot's `terraform` ecosystem proposes provider bumps for
  both stacks weekly. The CI checks below must pass before a bump merges.

### Review and verification

- `make tf-check` runs in CI on every push and pull request. It runs
  `terraform fmt -check` and `validate` on both stacks, then `terraform test`
  with **mock providers** against `dev.tfvars` and `prod.tfvars`. The tests
  assert the security and scaling invariants: same image everywhere,
  ingress only on the API, managed-identity pulls and secret reads, the Key
  Vault-backed database URL, Entra-only telemetry, per-secret RBAC, tags, and
  delete locks when protected. Last comes Checkov at a digest-pinned version.
  None of this needs Azure credentials.
- Checkov findings are either fixed or skipped inline with a
  `#checkov:skip=<id>:<reason>` comment. Each skip names its ADR justification,
  so every accepted risk shows up in code review.
- `make tf-plan` saves the plan to a file, and `make tf-apply` applies only
  that saved plan. Nothing is applied that was not reviewed.

## Consequences

- Infrastructure changes are reviewable diffs with a saved plan, and the
  invariants are enforced by tests rather than by convention.
- Bootstrapping is a documented one-time step per subscription
  ([runbook](../operations/azure-runbook.md)). Everything else, including
  teardown, is `terraform` plus a few `az` commands for images and jobs.
- State holds the database password, which makes state access a security
  boundary. Moving to Microsoft Entra database authentication (ADR-0012)
  removes the password from state.
- Terraform plans cannot run without Azure credentials, so CI stops at mock
  tests. Plan-in-CI with OIDC federation is part of Phase 9.
