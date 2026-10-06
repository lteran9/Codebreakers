# ADR-0010: Container Image and Local Production Stack

## Status

Accepted. Extends [ADR-0009](ADR-0009-async-analysis-worker.md) with a
PostgreSQL queue backend.

## Context

Phase 7 packages the API, outbox relay, and analysis worker as container
images and runs them together locally, with health checks, migrations, and
graceful shutdown that behave like production. The deployed system uses Azure
Service Bus, which has no local substitute that fits a single
`docker compose up`. CI image builds and registry publishing belong to Phase 9.
This phase adds only local build, scan, and verification tooling.

## Decision

### One image, three roles

A single image serves every process. `ENTRYPOINT ["codebreakers"]`, and the
command selects the role: `serve` (the default `CMD`), `relay`, `worker`, or
`deadletter …`. Migrations run with `--entrypoint alembic`. The API and worker
always ship the same dependency set and release version. Each role still
scales and deploys independently, because the command is chosen per
container.

### Reproducible, minimal build

- **Multi-stage.** The build stage installs locked build tooling, builds the
  project wheel, and installs everything into `/opt/venv`. The runtime stage
  copies only that venv, `alembic.ini`, and `migrations/`. No compilers,
  tests, or development tools reach the runtime image.
- **Deterministic installs.** `requirements/runtime.txt` and
  `requirements/build.txt` are pip-tools lock files with SHA-256 hashes,
  installed with `--require-hashes --no-deps`. They are compiled by
  `make lock` inside the base image, so Linux environment markers resolve
  correctly on any host. A unit test fails when a `pyproject.toml` dependency
  is missing from the lock or no longer satisfied by it. Dependabot's pip
  ecosystem updates the lock files.
- **Pinned base.** `python:3.12-slim-trixie` is pinned by multi-arch index
  digest, as is `postgres:16-alpine` in Compose. Dependabot's `docker` and
  `docker-compose` ecosystems propose digest bumps. Debian 13 replaced
  Debian 12 after an initial scan: bookworm carried five critical OS
  vulnerabilities, trixie carries none.
- **Least privilege.** The runtime runs as UID/GID 10001, and pip is removed
  from it. `.dockerignore` is an allowlist, so `.env`, virtual environments,
  caches, tests, and Git data never enter the build context.

### PostgreSQL queue substitute

`CODEBREAKERS_ANALYSIS_QUEUE=postgres` selects a queue stored in the
`analysis_job_queue` table (migration `0003`). The Service Bus emulator was
rejected. It needs its own SQL Server sidecar and a JSON topology file, which
roughly doubles the stack for one dependency. The PostgreSQL queue reuses the
database the stack already runs:

- **Same topology.** The relay still moves outbox rows to the queue, and
  workers consume them independently, so the local stack has the deployed
  shape: API, relay, and worker.
- **Peek-lock semantics.** A receiver claims ready rows with
  `FOR UPDATE SKIP LOCKED` in a short transaction. Each claim gets a lock
  token and a `locked_until` lease of the time budget plus one minute. No
  transaction stays open during an analysis. Complete, abandon, and
  dead-letter succeed only for the current lock token. A message whose lock
  lapsed is redelivered, and once `delivery_count` reaches 10 it is
  dead-lettered as `MaxDeliveryCountExceeded`, like the Service Bus backstop.
  All timestamps come from the database clock.
- **Shared settlement.** Message settlement and the dead-letter replay and
  discard logic moved from the Service Bus adapter into
  `infrastructure/messaging/settlement.py`. Both brokers use one
  `JobConsumer` and one `DeadLetterQueue` protocol, and
  `codebreakers deadletter …` works against either. The PostgreSQL adapter
  passes the shared `JobPublisher` contract suite.

The PostgreSQL queue is a local and test substitute. Azure deployments keep
Service Bus.

> **Amended in Phase 8:** the first Azure environment also uses the PostgreSQL
> queue, with KEDA `postgresql` scale rules, and defers Service Bus. See
> [ADR-0012](ADR-0012-azure-dev-environment.md).

### Startup, health, and shutdown

- **One-shot migration.** The `migrate` service runs `alembic upgrade head`
  once, after PostgreSQL is healthy. The API, relay, and worker wait for it
  with `service_completed_successfully`. API replicas never compete to
  migrate.
- **Health.** PostgreSQL uses `pg_isready` over TCP. The init-time server
  listens only on the Unix socket, so a socket check would report ready too
  early. The API is probed at `/health/ready`. The relay and worker have no
  HTTP server. When `CODEBREAKERS_HEARTBEAT_FILE` is set, they touch that file
  on every loop iteration, and `python -m codebreakers.worker.heartbeat` fails
  if it is older than 120 s. That window is longer than the 30 s time budget
  plus the receive wait, so a busy worker still looks alive.
- **Graceful shutdown.** Python runs as PID 1 and installs its own SIGTERM
  handlers, so no init process is needed. Uvicorn drains in-flight requests
  for `--graceful-timeout` seconds (default 10). The relay and worker finish
  the current batch or message and exit 0. The worker's `stop_grace_period`
  (45 s) is longer than the analysis time budget. `make shutdown-check`
  asserts a clean exit and the shutdown log line for each service.
- **Hardening.** Every container is `read_only` with a `tmpfs` `/tmp`, runs
  with `cap_drop: [ALL]` and `no-new-privileges`, and runs as a non-root
  user. PostgreSQL runs as its image's `postgres` UID 70. The API binds to
  `127.0.0.1` on the host, and PostgreSQL is not published.

### Scanning and SBOM

`make scan` runs a digest-pinned Trivy against a `docker save` export of the
image. It reports HIGH and CRITICAL vulnerabilities. It fails on any CRITICAL
vulnerability not listed in the reviewed `.trivyignore`, and on any secret
found in the layers or image configuration. `make sbom` writes a CycloneDX
SBOM to `build/container/sbom.cdx.json`. `make image-check` asserts the
non-root user and the absence of pip and development tools. It also checks
for credential-like values in the environment and build history. Phase 9
moves these checks into CI.

## Consequences

- `docker compose up --build --wait` starts a production-like stack with no
  cloud credentials, and `make smoke` exercises it end to end.
- Lock files must be regenerated with `make lock` when dependencies change,
  and the unit test enforces this.
- The PostgreSQL queue adds load to the database, with polling every 0.5 s
  per idle worker. That is acceptable locally but is not the production
  design.
- HIGH OS vulnerabilities in the base image are reported but do not fail the
  scan. Most have no Debian fix yet, and digest bumps pick fixes up.
