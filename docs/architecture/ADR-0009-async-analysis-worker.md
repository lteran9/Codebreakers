# ADR-0009: Asynchronous Analysis Worker

## Status

Accepted. Amends [ADR-0007](ADR-0007-http-api-contract.md) (status code of
`POST /v1/analyses`) and [ADR-0008](ADR-0008-analysis-persistence.md) (source
text retention). [ADR-0010](ADR-0010-containers-local-stack.md) adds the
`postgres` queue backend used by the local container stack.

## Context

The roadmap gates the queue and worker on evidence that an analyzer's p95
runtime exceeds the HTTP request budget. **That evidence does not exist yet.**
The slowest analyzer (Vigenere) takes about 1.2 s at the 20,000-character cap
(ADR-0007), and no long-running solver such as substitution hill-climbing has
been built.

**This phase is built ahead of demand, as a portfolio demonstration** of
durable asynchronous processing: outbox publishing, idempotent consumers,
bounded retries, budgets, and dead-letter operations. It is not a response to a
measured latency problem. Expensive solvers added later will use it without
changes to the API contract.

## Decision

### Flow

```text
POST /v1/analyses ──► one DB transaction: job (pending) + input + outbox row
                                   │
            codebreakers relay ◄───┘  SELECT … FOR UPDATE SKIP LOCKED
                    │ publish {schema_version, job_id, attempt, trace}
                    ▼
             Service Bus queue ──► codebreakers worker ──► claim, run, record
```

- **API:** `POST /v1/analyses` validates the analyzer and language, then
  returns **`202 Accepted`** with a `pending` job and a `Location` header.
  Clients poll `GET /v1/analyses/{id}`. The resource shape is unchanged. Only
  the status code changed from `201` and jobs are no longer terminal on
  creation. Analyzer failures such as insufficient text are now reported as a
  `failed` job with an `error_code` instead of a synchronous 422.
- **Message schema:** `AnalysisJobMessage` is versioned JSON containing
  `schema_version` (currently 1), `job_id`, `attempt`, `correlation_id`, and
  `traceparent`. It never contains the source text. Consumers reject unknown
  versions as poison messages.
- **Transactional outbox:** The job, its input, and an outbox row are
  committed atomically, so a broker outage at submission time loses nothing.
  `codebreakers relay` publishes and deletes rows. It locks them with
  `FOR UPDATE SKIP LOCKED`, so any number of relays can run. If publishing
  fails, the deletions already made are committed and the rest stay queued.
  Delivery is therefore *at least once*.
- **Source text:** Text is stored in `analysis_job_inputs` only until the job
  reaches a terminal state, alongside the correlation ID and traceparent so
  recovered jobs keep their request context. It is deleted in the same transaction as that
  transition. Rows left behind by a stuck job cascade-delete with the job when
  the retention purge runs.
- **Ports:** `JobPublisher` and `AnalysisOutbox` live in the application
  layer. Adapters: `ServiceBusJobPublisher` (Azure Service Bus),
  `PostgresJobPublisher` (local stack, ADR-0010), and `InProcessJobQueue`.
  All pass the shared contract suite in
  `tests/test_job_publisher_contract.py`.

### Worker semantics

- **Atomic claim:** A worker claims a job with an optimistic, version-checked
  update that moves it to `running` and increments `attempts`. It also sets
  `lease_expires_at` to the time budget plus a grace period. A `running` job
  whose lease has lapsed can be claimed again. That recovers work from a
  worker that was killed mid-analysis.
- **Idempotency:** If a message arrives for a terminal job or an unknown
  (purged) job, it is completed without doing any work. A duplicate that
  arrives while another worker holds the lease is rescheduled for when the
  lease expires. If the lease lapsed and another worker took over, the slower
  worker's result loses the version check and is dropped.
- **Budgets and cancellation:** Each analysis runs in a spawned child process
  under a wall-clock budget (`CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS`,
  default 30). The budget includes about 0.1–0.3 s of child start-up. On
  POSIX the child also sets `RLIMIT_CPU` and `RLIMIT_AS`
  (`CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB`, default 1024, `0` disables;
  `RLIMIT_AS` is ignored where unsupported, e.g. macOS). A child that
  overruns is killed and the job becomes **`cancelled`** with
  `error_code` `time-budget-exceeded` or `memory-budget-exceeded`.
  Cancellation is internal only; there is no public cancel endpoint.
- **Failure classes:** Analyzer errors are final. The job becomes `failed`
  with a stable code such as `insufficient-text`. An unexpected child exit, or
  any infrastructure error such as a database outage, is transient: the job is
  released to `pending`, and the message is rescheduled as `attempt + 1`.
- **Bounded retries:** Retries back off exponentially using scheduled
  messages (`CODEBREAKERS_WORKER_RETRY_BASE_SECONDS`=2 doubling up to
  `…_RETRY_MAX_SECONDS`=60) for up to `CODEBREAKERS_WORKER_MAX_ATTEMPTS`=5
  attempts. After the last attempt the job is marked `failed`
  (`retries-exhausted`) if the database is reachable, and the message is
  dead-lettered. Configure the queue's `MaxDeliveryCount` (for example 10) as
  a backstop for crashes that never reach settlement.
- **Settlement:** Messages are received in peek-lock mode with
  `AutoLockRenewer` covering the time budget plus one minute. Each one is
  then completed, rescheduled and completed, or dead-lettered. If settlement
  fails, the message is abandoned so the broker redelivers it.
- **Shutdown:** SIGTERM/SIGINT stop receiving after the current message. Set
  the platform termination grace period longer than the time budget. If a
  worker is killed instead, the lease and lock expiry recover the job.
- **Trace context:** A well-formed W3C `traceparent` request header and the
  correlation ID (`X-Request-ID`) are validated, carried in the message body,
  and mirrored into the Service Bus `correlation_id` and application
  properties. Worker logs include them. OpenTelemetry export is deferred to
  Phase 9.
- **Dead letters:** `codebreakers deadletter list | replay | discard`
  operates on message metadata only. See the
  [dead-letter runbook](../operations/dead-letter-runbook.md).

### Deployment topology

| Process | Command | Needs |
| --- | --- | --- |
| API | `codebreakers serve` | PostgreSQL |
| Relay | `codebreakers relay` | PostgreSQL, plus Service Bus unless the backend is `postgres` |
| Worker | `codebreakers worker` | PostgreSQL, plus Service Bus unless the backend is `postgres` |

Select the backend with `CODEBREAKERS_ANALYSIS_QUEUE`:

- `service-bus`: the API only records jobs, so the API, relay, and worker
  scale and deploy independently. PostgreSQL is required.
- `postgres`: the same distributed topology, with the queue stored in a
  PostgreSQL table instead of Service Bus. It has peek-lock semantics, lock
  tokens, and a dead-letter state. It is used by the Docker Compose stack and
  integration tests. See [ADR-0010](ADR-0010-containers-local-stack.md).
- `in-process` (default): the API lifespan hosts the relay and one worker
  thread over an in-memory queue. A single local process, or a test, runs jobs
  end to end without Azure. The in-memory queue does not survive a restart, so
  on start the runtime re-publishes every `pending` or `running` job that has
  no outbox row. Leases and idempotency make the duplicates safe.

Service Bus is reached with `CODEBREAKERS_SERVICEBUS_CONNECTION_STRING` and
`CODEBREAKERS_SERVICEBUS_QUEUE` (default `analysis-jobs`). Managed identity
replaces the connection string when Azure infrastructure arrives (Phase 8).

## Consequences

- Submission latency no longer depends on analyzer runtime, and workers scale
  independently of the API.
- Clients must poll for completion, and a duplicate message costs one database
  read.
- Source text now lives in the database briefly. ADR-0008's retention purge
  remains mandatory, and backups of `analysis_job_inputs` are sensitive.
- Running a child process per job adds tens of milliseconds and isolates
  runaway analyzers. The in-process backend runs one job at a time.
- Service Bus integration tests require a dedicated namespace and run only
  through the manually triggered `Service Bus integration` workflow. Unit
  tests cover the adapter with fakes.
