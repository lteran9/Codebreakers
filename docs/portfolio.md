# Portfolio and Interview Notes

Notes for presenting Codebreakers in a CV or interview. Every claim here is
either measured, with the date and the command to reproduce it, or links to
the code, test, or decision record that backs it. The last section lists
claims this project does **not** support.

## What it is, in one paragraph

Codebreakers is a typed Python 3.12 service for classical ciphers (Caesar,
substitution, Vigenère, homophonic) and keyless cryptanalysis. The same
domain core is exposed through a CLI and a FastAPI HTTP API. Long analyses
run as durable asynchronous jobs: PostgreSQL, a transactional outbox, a
relay, and a worker. It is deployed with Terraform to Azure Container Apps,
with OIDC-based delivery, staged rollout, and OpenTelemetry. It was built in
ten phases between 2026-09-13 and 2026-10-12, each with its own decision
records and exit criteria ([TODO.md](../TODO.md)).

## Measured facts

| Fact | Value | Source / how to reproduce |
| --- | --- | --- |
| Cipher families / analyzers | 4 / 4 | [domain.md](domain.md); an unsupported-cipher error lists them |
| Tests | 454 collected: 450 pass, 4 skipped (live Service Bus only) | `pytest`, 2026-10-12 |
| Line coverage | 94.4 % with integration tests; 88.8 % unit only (gate: 80 %) | `pytest`; `make test` |
| Property-based tests | 4 Hypothesis properties (3 cipher round trips, 1 message schema) | [test_cipher_catalog.py](../tests/test_cipher_catalog.py), [test_job_messages.py](../tests/test_job_messages.py) |
| Code size | 7,145 lines of source in 56 modules; 5,983 lines of tests; 1,286 lines of Terraform | `git ls-files … \| xargs cat \| wc -l` |
| Type checking | `mypy --strict` clean on `src` and `tests` | `make typecheck` |
| Container image | 394 MB uncompressed (arm64); non-root (uid 10001); no pip or build tools | `docker image ls`; `make image-check` |
| CI quality gates on `master` | 5 parallel jobs, about 2 minutes wall time | GitHub Actions run 38079016483, 2026-10-10 |
| Slowest analyzer at the 20,000-character cap | Vigenère, 1.1 s (Apple M5 Pro) | `make benchmark`, [benchmarks.md](benchmarks.md) |
| Azure warm API latency | p50 47 ms, p95 57 ms (50 encryptions, client over the internet) | `make azure-measure`, 2026-10-12 |
| Azure cold start | 42 s first response; ~60 s first queued job | Same run |
| Azure warm queued job | median 2.7 s from submission to result | Same run |
| Azure `dev` spend | $0.62 for the first ~3 days of October (≈ $0.21/day). PostgreSQL showed $0 in that window, so the full list-price estimate (~$21–27/month) is the safer number to quote. | Azure Cost Management, `rg-codebreakers-dev`, 2026-10-12 |
| Decision records | 17 ADRs, each with alternatives considered | [architecture/README.md](architecture/README.md) |
| Runbooks | Azure operations, rollback, database restore, dead letters, credential compromise, and one per alert | [operations.md](operations.md) |

## Two-minute architecture talk track

> Codebreakers started as a pure Python library: four classical ciphers
> behind one `Cipher` protocol, and analyzers behind an `Analyzer`
> protocol. The domain has no framework imports. That made the next steps
> cheap: the CLI and the FastAPI app are thin adapters over the same
> application services, and adding a cipher means one class and one registry
> entry.
>
> The interesting part is analysis. Recovering a Vigenère key is the
> slowest operation, about a second at the input cap, so the API accepts
> the job, returns 202 with a Location header, and a worker does the work.
> The job and an outbox row are committed in one PostgreSQL transaction, a
> relay publishes the outbox to a queue, and workers claim jobs with a lease.
> A job is a small state machine: pending, running, succeeded, failed, or
> cancelled. Every change is a compare-and-set on a version number, so
> duplicate deliveries and crashed workers are safe. I'm upfront that this
> was built ahead of demand. Synchronous analysis would be fine today, and
> ADR-0009 says so. I built it to show durable processing done properly.
>
> In Azure, the API, relay, and worker are three Container Apps running the
> same image. They scale to zero, and KEDA wakes the relay and worker based
> on PostgreSQL row counts. The apps use a managed identity for the
> registry, Key Vault, and telemetry. GitHub Actions deploys through OIDC,
> with no stored credentials. Each release becomes a new API revision with
> no traffic. It's smoke-tested at its own URL, then traffic shifts to it,
> and the previous revision stays active for rollback.

## Discussion examples

### A design I rejected: Service Bus in the deployed environment

The plan targeted Azure Service Bus, and the adapter exists. It is
contract-tested and has a manual live-integration workflow. For `dev` I
deployed a **PostgreSQL-backed queue** instead (`FOR UPDATE SKIP LOCKED`
with leases), because the database already exists, it costs nothing extra,
and KEDA can scale on a SQL query. Choosing the queue is one environment
variable, and the substitution and the way back are written down in
[ADR-0012](architecture/ADR-0012-azure-dev-environment.md). The point to make:
a portable port with two adapters let me make the cheaper choice without
giving up the other.

Also rejected: publishing to the queue directly from the request handler.
That loses jobs if the broker is down between the database commit and the
publish, so the outbox was chosen
([ADR-0009](architecture/ADR-0009-async-analysis-worker.md)).

### A simulated production failure: a worker dies mid-job

If a worker is killed after claiming a job, the job stays `running` with a
lease. When the message is redelivered after the lease expires, the next
worker reclaims the job and finishes it as attempt 2. The same guarantees
cover these cases:

- a worker killed mid-job:
  [test_worker_terminated_mid_job_is_recovered_after_lease_expiry](../tests/test_worker_processing.py);
- jobs accepted while no worker was running:
  [test_jobs_accepted_during_a_worker_outage_complete_after_recovery](../tests/test_postgres_repository.py),
  run against real PostgreSQL;
- an analysis that hangs: the worker runs analysis in a child process and
  kills it at the 30 s budget
  ([test_process_executor_kills_analysis_that_exceeds_its_time_budget](../tests/test_worker_execution.py));
- a SIGTERM during a deployment: CI's `make shutdown-check` stops the
  running API, relay, and worker containers and checks that each one logs a
  clean shutdown and exits 0 instead of being killed.

### A security and cost trade-off

`dev` uses password authentication to PostgreSQL, with the password held in
Key Vault, and a firewall rule that allows "Azure services". That rule
includes other tenants' workloads, because consumption Container Apps have
no fixed outbound IP. Microsoft Entra database auth plus private networking
would close both gaps. They would also add SQL-provisioned roles, a
token-refresh hook, and VNet costs that a demo does not justify. The gap is
named as the weakest boundary in
[ADR-0012](architecture/ADR-0012-azure-dev-environment.md#trust-boundaries-and-public-endpoints), with
the path back. Everything else (registry, Key Vault, telemetry, deployment)
is keyless.

The cost side is scale to zero. It keeps the environment at a few dollars a
month, and the price is a measured ~42 s cold start and ~1 minute for the
first queued job.

## CV wording

Use one of these as written, or shorten. Each phrase is backed above.

> **Codebreakers** — Python 3.12, FastAPI, PostgreSQL, Azure Container Apps,
> Terraform, GitHub Actions, OpenTelemetry
>
> - Built a typed (mypy `--strict`) cipher and cryptanalysis service with a
>   clean-architecture core shared by a CLI and an HTTP API. It has 450+
>   tests at 94 % coverage, including Hypothesis property tests and
>   PostgreSQL integration tests.
> - Designed durable asynchronous analysis jobs: a transactional outbox, a
>   relay, and leased, idempotent workers with retries and dead-lettering.
>   They run on a PostgreSQL queue in Azure, with an Azure Service Bus
>   adapter covered by the same contract tests.
> - Deployed to Azure Container Apps with Terraform. Workloads use
>   managed-identity access; delivery uses OIDC with no stored credentials;
>   releases go to a zero-traffic revision, are smoke-tested, then traffic
>   shifts; rollback is one workflow; and I wrote runbooks for each alert.

Do **not** write "Service Bus workers" without qualification. The deployed
system uses the PostgreSQL queue.

## Claims this project does not support

- **No production traffic or load testing.** Measurements are single-client
  samples (see [benchmarks.md](benchmarks.md)). There is no capacity figure
  or SLO.
- **No zero-downtime guarantee.** Staged rollout and a warm previous revision
  reduce risk. Dropped requests during a traffic shift have not been
  measured.
- **The automated deploy has not run live yet.** The CI quality gates pass,
  but the *Deploy to dev* job fails at Azure sign-in until the GitHub
  environment is configured. The running `dev` revision was deployed with
  the documented manual flow. A rollback drill and alert tests on Azure are
  also still pending.
- **`prod` is not provisioned.** It is defined and tested only with
  Terraform mock providers.
- **No security guarantee.** The ciphers provide no confidentiality by
  design. The service has had no external security review or penetration
  test, and the threat model is self-assessed.
- **The cryptanalysis is not novel.** It is textbook frequency analysis,
  and it fails on short texts (see [domain.md](domain.md#limitations)).
