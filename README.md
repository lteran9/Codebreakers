# Codebreakers

Codebreakers is a Python project for exploring classical ciphers, historical encoded messages, and clean engineering practices. The repository is intentionally structured around a domain-first design so that cipher logic stays independent from CLI, API, and infrastructure concerns.

> **Security notice:** the ciphers here provide **zero confidentiality**. They are broken by design, and this project exists to break them. Never use them to protect real secrets. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md). The [threat model](docs/security/threat-model.md) covers the hosted service.

## Project goals

- Build a small, typed cipher domain with reproducible examples.
- Keep behavior testable without framework coupling.
- Establish quality gates before later phases expand the project.
- Document early architectural decisions and the text-normalization policy.

## Development setup

Create and activate a virtual environment, then install the project with its development tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pre-commit install
```

Common repository commands are available through the Makefile:

```bash
make install
make format
make lint
make typecheck
make test
make coverage
```

## Quality gates

Run the fast checks locally with:

```bash
make test
ruff check .
ruff format --check .
mypy src tests
```

PostgreSQL integration tests use Testcontainers and require Docker:

```bash
make integration
```

`make scan-source` runs Trivy over the locked requirements (fail on CRITICAL) and the working tree (fail on any secret), as CI does.

## Command-line usage

After installing the package, the `codebreakers` console script is available:

```bash
# Encrypt with a direct --text argument
codebreakers encrypt --cipher caesar --key 3 --text "HELLO WORLD"
# KHOOR ZRUOG

# Decrypt by piping input through standard input
echo "KHOOR ZRUOG" | codebreakers decrypt --cipher caesar --key 3
# HELLO WORLD

# Use a custom alphabet
codebreakers encrypt --cipher caesar --key 2 --text "1239" --alphabet "0123456789"
# 3451

# Use Vigenere
codebreakers encrypt --cipher vigenere --key LEMON --text "ATTACK AT DAWN"
# LXFOPV EF RNHR

# Use monoalphabetic substitution
codebreakers encrypt --cipher substitution --key QWERTYUIOPASDFGHJKLZXCVBNM --text "ATTACK"
# QZZQEA
```

Run `codebreakers --help`, `codebreakers encrypt --help`, or `codebreakers decrypt --help` for full option details.

Analyze ciphertext without a key; the report is printed as JSON:

```bash
codebreakers analyze --analyzer caesar-bruteforce \
  --text "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"
```

Analyzers: `caesar-bruteforce`, `substitution-frequency`, `vigenere-frequency`, `homophonic-distribution`. Exit codes are 0 (success), 1 (unexpected error), 2 (invalid usage or unsupported language), 3 (invalid key), 4 (invalid alphabet), 5 (unsupported cipher), 6 (unsupported analyzer), and 7 (insufficient text for analysis).

## HTTP API

Start the API locally (binds to `127.0.0.1:8000` by default), then open `http://127.0.0.1:8000/docs` for interactive OpenAPI documentation:

```bash
codebreakers serve
```

```bash
# Encrypt and decrypt
curl -s -X POST http://127.0.0.1:8000/v1/ciphers/caesar/encrypt \
  -H 'Content-Type: application/json' \
  -d '{"text": "HELLO WORLD", "key": "3"}'
# {"cipher":"caesar","operation":"encrypt","result":"KHOOR ZRUOG"}

# Submit an analysis (returns 202 with a pending job and a Location header),
# then poll it until status is succeeded, failed, or cancelled
curl -s -i -X POST http://127.0.0.1:8000/v1/analyses \
  -H 'Content-Type: application/json' \
  -d '{"analyzer": "caesar-bruteforce", "text": "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"}'
curl -s http://127.0.0.1:8000/v1/analyses/<id>

# Probes
curl -s http://127.0.0.1:8000/health/live
curl -s http://127.0.0.1:8000/health/ready
```

Errors are RFC 9457 `application/problem+json` bodies with a machine-readable `code` and a `correlation_id` that matches the `X-Request-ID` response header. Request bodies are limited to 64 KiB, text to 20,000 characters, and keys to 4,096 characters. Analysis jobs are stored in memory by default. Set `CODEBREAKERS_DATABASE_URL` to use PostgreSQL, apply schema changes with `make migrate`, and schedule `python -m codebreakers.infrastructure.persistence.retention` daily. Results are retained for seven days by default (`CODEBREAKERS_ANALYSIS_RETENTION_DAYS`). Submitted source text is kept only until its job finishes. See [ADR-0007](docs/architecture/ADR-0007-http-api-contract.md) for the HTTP contract and [ADR-0008](docs/architecture/ADR-0008-analysis-persistence.md) for persistence and retention decisions.

### Asynchronous analysis worker

Analyses run outside the request. The API commits the job with a transactional outbox entry, then returns `202 Accepted`. A worker claims the job, runs the analyzer in a child process within its time and memory budget, and records the outcome. Over budget, the job becomes `cancelled`. If the analyzer rejects the input, the job becomes `failed`, with an `error_code` such as `insufficient-text`. This was built ahead of measured demand, as a portfolio demonstration. See [ADR-0009](docs/architecture/ADR-0009-async-analysis-worker.md).

By default (`CODEBREAKERS_ANALYSIS_QUEUE=in-process`), `codebreakers serve` runs the relay and one worker thread in the same process, so no Azure resources are needed. For independently scaled processes on Azure Service Bus:

```bash
export CODEBREAKERS_ANALYSIS_QUEUE=service-bus
export CODEBREAKERS_DATABASE_URL=postgresql://…
export CODEBREAKERS_SERVICEBUS_CONNECTION_STRING='Endpoint=sb://…'
export CODEBREAKERS_SERVICEBUS_QUEUE=analysis-jobs       # default
codebreakers serve     # API: records jobs and outbox entries only
codebreakers relay     # publishes outbox entries (safe to run several)
codebreakers worker    # consumes jobs (scale horizontally)
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS` | `30` | Wall-clock limit per job, including child start-up |
| `CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB` | `1024` | `RLIMIT_AS` for the child process where supported (`0` disables) |
| `CODEBREAKERS_WORKER_MAX_ATTEMPTS` | `5` | Attempts before a message is dead-lettered |
| `CODEBREAKERS_WORKER_RETRY_BASE_SECONDS` / `_MAX_SECONDS` | `2` / `60` | Exponential backoff between attempts |
| `CODEBREAKERS_HEARTBEAT_FILE` | unset | File the worker and relay touch each loop, for container liveness probes |

`CODEBREAKERS_ANALYSIS_QUEUE=postgres` runs the same API, relay, and worker topology with the queue stored in PostgreSQL instead of Service Bus. The container stack below uses it. Every variable is listed with a fake value in [.env.example](.env.example).

A valid W3C `traceparent` header and the `X-Request-ID` correlation ID are propagated to the worker logs. To inspect, replay, or discard dead-lettered messages, use `codebreakers deadletter list | replay | discard`, as described in the [dead-letter runbook](docs/operations/dead-letter-runbook.md).

### Abuse limits and observability

The API limits each client IP, per replica, to `CODEBREAKERS_RATE_LIMIT_PER_MINUTE` requests (default 120) and `CODEBREAKERS_ANALYSIS_SUBMISSIONS_PER_MINUTE` analysis submissions (default 10). Requests over the limit get `429` with `Retry-After`. Once `CODEBREAKERS_MAX_PENDING_ANALYSES` jobs are unfinished (default 100), new submissions get `503 analysis-capacity-exceeded`. Setting any of these to `0` disables it.

`CODEBREAKERS_TRUSTED_PROXY_HOPS` is the number of reverse proxies whose `X-Forwarded-For` entries are trusted (default `0`; Azure sets `1`). `CODEBREAKERS_ANALYSIS_LISTING_ENABLED=false` turns off `GET /v1/analyses`, which then returns `404`. Azure sets it, so jobs are readable only by their unguessable ID.

Every process logs one JSON object per line, tagged with service, environment, version, revision, and correlation and job IDs. `CODEBREAKERS_LOG_FORMAT=text` gives readable lines, and `CODEBREAKERS_LOG_LEVEL` sets the level. Logs never contain submitted text, results, or keys; a test enforces this. When Application Insights is configured, OpenTelemetry traces follow a request through the relay and the worker, and the worker exports job and queue metrics. See [ADR-0014](docs/architecture/ADR-0014-observability.md).

The committed contract lives in [docs/api/openapi.json](docs/api/openapi.json). A test fails when the generated schema drifts; review the change and regenerate with `make openapi`.

These are broken classical ciphers. They provide no confidentiality and must never be used to protect real secrets.

## Containers and local stack

One image runs every role: the API by default, plus `relay`, `worker`, `deadletter …`, or `alembic upgrade head` via `--entrypoint alembic`. Start a production-like local stack with a single command. It brings up PostgreSQL, a one-shot migration, the API, the relay, and the worker, and waits until every service is healthy:

```bash
docker compose up --build --wait     # or: make up
make smoke                           # health, encrypt, submit analysis, worker completion, result
make shutdown-check                  # SIGTERM api/relay/worker; assert clean exits
docker compose down                  # add -v to delete the database volume
```

The database volume is `postgres18-data`, mounted at `/var/lib/postgresql` as the PostgreSQL 18 image requires. A `codebreakers_postgres-data` volume left over from older versions holds PostgreSQL 16 data that version 18 cannot read. Remove it with `docker volume rm codebreakers_postgres-data` once you no longer need it.

The API listens on `http://127.0.0.1:8000`. Copy [.env.example](.env.example) to `.env` to override defaults. Containers run read-only as a non-root user with all capabilities dropped. Image checks:

```bash
make image          # build codebreakers:local
make image-check    # non-root user, no pip/dev tools, no credentials in env or history
make scan           # Trivy: fail on unreviewed CRITICAL vulnerabilities or any secret
make sbom           # CycloneDX SBOM at build/container/sbom.cdx.json
make lock           # regenerate hashed requirements/*.txt after dependency changes
```

`codebreakers serve --graceful-timeout N` sets how long in-flight requests may drain after SIGTERM. See [ADR-0010](docs/architecture/ADR-0010-containers-local-stack.md) for the image, queue substitute, health, and shutdown decisions.

## Azure deployment

Terraform in [infra/](infra/) provisions an Azure Container Apps environment in `westus3`. It runs the API, relay, and worker from the same Git-SHA-tagged image, with PostgreSQL Flexible Server, Key Vault, Application Insights, and a budget alert. Idle compute scales to zero, and identity-based access covers the registry, secrets, and telemetry. CI checks the infrastructure without Azure credentials:

```bash
make tf-check       # fmt, validate, terraform test (mock providers, dev + prod), Checkov
```

For the first deployment, follow the runbook to bootstrap state and create the registry. With Docker Desktop running, build and push locally without ACR Tasks, then review and apply the plan:

```bash
make tf-init TF_STATE_ACCOUNT=<bootstrap output>
ACR=$(terraform -chdir=infra/azure output -raw container_registry_name)
ACR_HOST=$(terraform -chdir=infra/azure output -raw container_registry_login_server)
az acr login --name "$ACR"
docker buildx build \
  --platform linux/amd64 \
  --tag "$ACR_HOST/codebreakers:$(git rev-parse HEAD)" \
  --push .
make tf-plan        # saved plan: review it
make tf-apply       # applies exactly the saved plan
make azure-migrate  # alembic upgrade head as a Container Apps job
make azure-smoke    # smoke test against the public HTTPS URL
```

Commit intended source changes before building, and keep the same commit checked out through the plan so the image tag matches. `make azure-image` is an optional Azure-side build alternative only when the subscription supports ACR Tasks.

After the first deployment, GitHub Actions releases the application ([ADR-0013](docs/architecture/ADR-0013-continuous-delivery.md)):

- A push to `master` runs CI, pushes the tested image with a provenance attestation, and runs migrations. It then creates a new API revision with no traffic, smoke-tests it, and shifts traffic to it.
- A `v*` tag publishes a GitHub release with the image, SBOM, and attestations, then deploys to `production` after approval.
- Workflows sign in to Azure through OIDC, with one identity per GitHub environment, and no stored secrets.
- Terraform owns the infrastructure but ignores images and traffic.
- `make azure-deploy IMAGE_TAG=<sha>` and `make azure-rollback [ROLLBACK_TO=<revision>]` run the same scripts from a workstation.

Operational alerts (API 5xx responses, restarts, database saturation, dead letters, queue age) email the budget contacts. Each links to the [alerts runbook](docs/operations/alerts.md). Further runbooks cover [rollback](docs/operations/rollback-runbook.md), [database restore](docs/operations/database-restore-runbook.md), and [credential compromise](docs/operations/credential-compromise-runbook.md).

Follow the [Azure runbook](docs/operations/azure-runbook.md) for the one-time state bootstrap, first deployment, connecting GitHub Actions, scaling checks, and teardown. [ADR-0011](docs/architecture/ADR-0011-terraform.md) covers Terraform and state. [ADR-0012](docs/architecture/ADR-0012-azure-dev-environment.md) covers the deployment diagram, trust boundaries, cost, identities, and the documented substitutions: the PostgreSQL queue instead of Service Bus, and password database auth.

### Terraform security scanning with Checkov

[Checkov](https://www.checkov.io/) is a static analysis tool for infrastructure-as-code. It inspects the Terraform files under `infra/` against security and configuration policies, such as whether resources enable encryption, restrict access, and avoid insecure defaults. It does not deploy resources or require Azure credentials.

`make tf-check` runs Checkov after Terraform formatting, validation, and mock-provider tests. The scan runs in a Docker image pinned to a version and digest, so CI uses the same scanner build each time. A failed policy check fails the target. When a deliberate design choice triggers a check, the relevant Terraform resource documents an inline `#checkov:skip=<id>:<reason>` exception for reviewers to assess.

## Cryptanalysis and benchmarks

Phase 3 adds reusable text statistics, Caesar brute-force ranking, substitution frequency-analysis assistance, Vigenere key-length and candidate ranking, and homophonic token-distribution reports. Analyzer outputs include explicit language or model versions and explainable scores or measurements.

Run the deterministic benchmark script with:

```bash
python scripts/benchmark_caesar.py
```

The current quality target is that the Caesar brute-force analyzer places the documented Caesar fixture's key in the top-ranked candidate. Vigenere analysis reports ranked key lengths and candidate keys for sufficiently long ciphertext, but short inputs may not contain enough repeated structure for reliable recovery. Substitution and homophonic analysis are assistance/reporting features in this phase and do not claim to automatically solve arbitrary ciphertext.

The package source lives in `src/codebreakers`, and tests live in `tests`.

## Architecture notes

The engineering baseline includes a lightweight ADR set under `docs/architecture/` to make the repository's layout and decisions explicit. This helps future phases avoid accidental framework leakage and inconsistent data-handling choices.
