# Operations Guide

This is the entry point for running Codebreakers: configuration, deployment,
migrations, observability, backup and restore, rollback, cost control, and
teardown. Each section summarizes the essentials and links to the detailed
runbook. The underlying decisions are in the
[architecture index](architecture/README.md).

## Environments

| Environment | How to start | Queue | Database | Notes |
| --- | --- | --- | --- | --- |
| Single process | `codebreakers serve` | In-process thread | In memory | No dependencies. Jobs are lost on restart. |
| Local stack | `docker compose up --build --wait` | PostgreSQL table | PostgreSQL 18 container | Production-like: API, relay, worker, and a one-shot migration. |
| Azure `dev` | Terraform + GitHub Actions | PostgreSQL table | PostgreSQL Flexible Server B1ms | Scales to zero. Public HTTPS ingress. |
| Azure `prod` | Same definition, `prod.tfvars` | PostgreSQL table | Larger SKU, 14-day backups | Defined and tested with mock providers, **not provisioned**. |

The Azure Service Bus adapter is implemented and contract-tested
(`CODEBREAKERS_ANALYSIS_QUEUE=service-bus`), but no deployed environment uses
it yet ([ADR-0012](architecture/ADR-0012-azure-dev-environment.md)).

## Configuration

All configuration is through environment variables. [.env.example](../.env.example)
lists every one with a safe placeholder and a comment.

| Group | Variables | Defaults |
| --- | --- | --- |
| Topology | `CODEBREAKERS_ANALYSIS_QUEUE`, `CODEBREAKERS_DATABASE_URL`, `CODEBREAKERS_SERVICEBUS_*` | `in-process`, unset (in-memory) |
| Analysis budget | `CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS`, `…_MEMORY_LIMIT_MB` | 30 s, 1024 MiB |
| Retries | `CODEBREAKERS_WORKER_MAX_ATTEMPTS`, `…_RETRY_BASE_SECONDS`, `…_RETRY_MAX_SECONDS` | 5, 2 s, 60 s |
| Retention | `CODEBREAKERS_ANALYSIS_RETENTION_DAYS` | 7 |
| Abuse limits | `CODEBREAKERS_RATE_LIMIT_PER_MINUTE`, `…_ANALYSIS_SUBMISSIONS_PER_MINUTE`, `…_MAX_PENDING_ANALYSES` | 120, 10, 100 (0 disables) |
| Exposure | `CODEBREAKERS_TRUSTED_PROXY_HOPS`, `CODEBREAKERS_ANALYSIS_LISTING_ENABLED` | 0, `true` (Azure: 1, `false`) |
| Logging | `CODEBREAKERS_LOG_FORMAT`, `CODEBREAKERS_LOG_LEVEL`, `CODEBREAKERS_ENVIRONMENT` | `json`, `INFO`, `local` |
| Telemetry | `APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_CLIENT_ID` | Unset (export off) |
| Liveness | `CODEBREAKERS_HEARTBEAT_FILE` (relay and worker touch it each loop) | Unset |

In Azure, Terraform sets every value. The only secret, the database URL, is a
Key Vault reference and never a plain environment value
([ADR-0017](architecture/ADR-0017-workload-and-delivery-identity.md)).

## Processes

The same package (and the same container image) runs every role:

```bash
codebreakers serve --graceful-timeout 10   # API: stores jobs and outbox rows
codebreakers relay                         # publishes outbox rows to the queue (safe to run several)
codebreakers worker                        # claims and runs jobs (scale horizontally)
codebreakers deadletter list|replay|discard
alembic upgrade head                       # migrations (image: --entrypoint alembic)
python -m codebreakers.infrastructure.persistence.retention   # daily purge
```

With `CODEBREAKERS_ANALYSIS_QUEUE=in-process`, `serve` runs the relay and one
worker thread in the same process. With `postgres` or `service-bus`, run the
three processes separately against the same `CODEBREAKERS_DATABASE_URL`. See
[ADR-0010](architecture/ADR-0010-containers-local-stack.md) for health checks
and graceful shutdown.

## Deployment

**First deployment** (once per environment): bootstrap Terraform state,
build and push an image, then `make tf-plan` and `make tf-apply`, and run
migrations. Follow the [Azure runbook](operations/azure-runbook.md) §1–3.

**Routine releases** run in GitHub Actions
([ADR-0013](architecture/ADR-0013-continuous-delivery.md)):

1. A push to `master` passes every CI gate, then the *Deploy to dev* job
   pushes the SHA-tagged image and runs migrations.
2. It then creates an API revision with **no traffic**, smoke-tests that
   revision's URL end to end, and shifts traffic.
3. A `v*` tag publishes a GitHub Release (image, SBOM, provenance) and
   deploys to `production` after reviewer approval.

From a workstation, `make azure-deploy IMAGE_TAG=<sha>` runs the same script.

> **Current status (2026-10-12):** The CI gates pass on `master`. The
> *Deploy to dev* job fails at Azure sign-in because the `dev` GitHub
> environment and its variables are not configured yet. To fix this, complete
> [Azure runbook §4](operations/azure-runbook.md#4-connect-github-actions).
> The running `dev` revision was deployed with the manual Phase 8 flow.

## Database migrations

- Alembic owns the schema. Run `alembic upgrade head` (`make migrate`). It
  runs in the Compose `migrate` service locally and as the `migrate`
  Container Apps job in Azure (`make azure-migrate`). API replicas never
  migrate.
- Migrations must be **backward compatible** (expand, then contract). The
  previous API revision keeps serving during a rollout, and a rollback does
  not reverse migrations.
- CI applies every migration up, down to base, and up again against a real
  PostgreSQL container (`make integration`).

## Observability

- **Logs:** every process writes one JSON object per line, tagged with
  `service`, `environment`, `version`, `revision`, `correlation_id`, and
  `job_id`. Submitted text, results, and keys are never logged, and a test
  enforces this.
- **Correlation:** a client's `X-Request-ID` (or a generated one) and a W3C
  `traceparent` travel from the API through the outbox and queue to the
  worker. One ID finds a request and its job.
- **Traces and metrics:** OpenTelemetry exports to Application Insights
  through the managed identity. Spans cover HTTP routes, SQL statements, and
  the queue (a producer span on publish and a consumer span in the worker).
  Metrics cover job outcomes and duration, and queue depth and age.
- **Alerts:** API 5xx responses, replica restarts, database saturation, dead
  letters, and queue age. Each alert emails the budget contacts and has a
  deliberate test procedure in the [alerts runbook](operations/alerts.md).

The KQL queries and design are in [ADR-0014](architecture/ADR-0014-observability.md).

## Backup and restore

- PostgreSQL Flexible Server keeps automated, locally redundant backups with
  point-in-time restore: 7 days in `dev`, 14 in `prod`.
- To restore, restore to a **new** server, verify the data, then copy it back
  into the Terraform-managed server, so Terraform and Key Vault stay
  unchanged. See the [database restore runbook](operations/database-restore-runbook.md).
- Backups can contain short-lived source text and up to 7 days of results.
  Treat them as sensitive ([threat model](security/threat-model.md)).

## Rollback

- **Preferred:** run the **Rollback** workflow in GitHub Actions, or `make
  azure-rollback [ROLLBACK_TO=<revision>]`.
- It moves API traffic back to the previous revision (reactivating it if
  needed), realigns the worker, relay, and jobs to that image, and
  smoke-tests the result.
- The previous revision stays active after each release, so rollback is a
  traffic switch rather than a rebuild.
- See the [rollback runbook](operations/rollback-runbook.md), which also
  covers schema problems and the rollback drill.

Other incident runbooks:
[dead letters](operations/dead-letter-runbook.md) and
[credential compromise](operations/credential-compromise-runbook.md).

## Cost control

- `dev` is sized for a demo. The list-price estimate is **~$21–27 a month**,
  mostly PostgreSQL B1ms and the Basic container registry. Idle Container
  Apps scale to zero. See the [ADR-0012 cost table](architecture/ADR-0012-azure-dev-environment.md#cost).
- A resource-group budget ($40 in `dev`) alerts at 80% and 100% of actual
  spend, and at 100% of forecast.
- Between demos, `az postgres flexible-server stop` saves about $12 a month.
  Azure restarts a stopped server after 7 days.
- Observed spend is recorded in [portfolio.md](portfolio.md#measured-facts).

## Shutdown and teardown

To pause a demo without losing data:

```bash
az postgres flexible-server stop -g rg-codebreakers-dev -n <server>
```

Container Apps already scale to zero when idle.

To remove the environment completely:

```bash
terraform -chdir=infra/azure destroy -var-file=environments/dev.tfvars \
  -var image_tag=$(git rev-parse HEAD)
```

`dev` has no delete protection, so this deletes the database, its backups,
and the Key Vault **irreversibly**. The bootstrap state account remains, and
`prod` refuses to be destroyed while its locks exist. See
[Azure runbook: Teardown and recreate](operations/azure-runbook.md#teardown-and-recreate).

Local stack: `docker compose down` (add `-v` to delete the database volume).
