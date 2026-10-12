# ADR-0017: Workload and Delivery Identity

## Status

Accepted. Recorded in Phase 10. It consolidates the identity decisions made
in [ADR-0012](ADR-0012-azure-dev-environment.md) (workload identity) and
[ADR-0013](ADR-0013-continuous-delivery.md) (delivery identity).

## Context

Two kinds of non-human principal need Azure access:

- **Workloads:** the API, relay, worker, and jobs. They pull images, read the
  database URL, and send telemetry.
- **Delivery:** GitHub Actions. It pushes images and updates revisions, jobs,
  and traffic.

Long-lived secrets in either place would be the most likely credential leak
in a public repository.

## Decision

| Principal | Identity | Grants | Credential |
| --- | --- | --- | --- |
| Workloads (all apps and jobs) | One user-assigned managed identity, `id-codebreakers-<env>` | AcrPull on the registry; Key Vault Secrets User on the `database-url` secret only; Monitoring Metrics Publisher on Application Insights | None. The platform issues tokens. |
| GitHub Actions | One user-assigned managed identity per environment, `id-codebreakers-<env>-deploy` | Contributor on the environment resource group; AcrPush on its registry | OIDC federated credential trusting only `repo:<owner>/<repo>:environment:<env>` |
| Operator | Their own Entra user (`az login`) | Subscription access for Terraform, plus Key Vault Secrets Officer to write the database secret | Interactive sign-in |

- The registry's admin user and Application Insights local authentication
  are disabled, so identity is the only way in.
- GitHub environments store only non-secret identifiers (client, tenant, and
  subscription IDs, and resource names), taken from a Terraform output.
- The only password is PostgreSQL's, held in Key Vault and injected through
  Container Apps Key Vault references (see ADR-0012 substitutions).

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Service principal with a client secret in GitHub secrets | A long-lived, exfiltratable secret that needs rotation. OIDC tokens are short-lived and bound to one repository environment. |
| Registry admin credentials or connection strings | Shared secrets with broad rights, and no per-caller audit trail. |
| System-assigned identity per app | Every app runs the same image with the same needs. Separate identities would add role assignments without narrowing access, and Key Vault references must exist before the app is created. |
| Separate identities for API, relay, and worker | Worth doing once the roles' permissions diverge, for example Service Bus Sender for the relay and Receiver for the worker. They do not diverge today. |
| Microsoft Entra authentication to PostgreSQL | Deferred: it needs SQL-created roles and a token-refreshing connection hook. The path back is documented in ADR-0012. |

## Consequences

- The repository and its GitHub environments hold no Azure secrets. Rotating
  access means changing a role assignment or a federated credential, not a
  password.
- Contributor on the resource group is broader than deploying needs. It is
  the narrowest built-in role that can update revisions and traffic, and it
  cannot grant access. A custom role is the tightening step.
- Production deployments run only from the `production` GitHub environment,
  which requires reviewer approval.
- The database password remains the one secret to rotate. The
  [credential compromise runbook](../operations/credential-compromise-runbook.md)
  covers it.
