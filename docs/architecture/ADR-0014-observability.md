# ADR-0014: Observability

## Status

Accepted. Extends [ADR-0009](ADR-0009-async-analysis-worker.md) (trace
context in job messages) and [ADR-0012](ADR-0012-azure-dev-environment.md)
(Application Insights with Entra-only ingestion).

## Context

A request is served by one process, and its analysis runs minutes later in
another, after passing through an outbox, a relay, and a queue. When a job
fails, an operator needs to find the request, its job, and the reason, and to
learn about systemic problems from alerts rather than from users. Users submit
arbitrary text, and some of it may be private (for example, a family letter
being deciphered). Telemetry must therefore describe what happened without
retaining what was submitted.

## Decision

### Logs

- **Format.** Every process logs one JSON object per line to stdout:
  `timestamp`, `level`, `logger`, `event`, `message`, `service`, `environment`,
  `version`, and `revision`, plus `correlation_id` and `job_id` when they are
  known. `CODEBREAKERS_LOG_FORMAT=text` gives readable lines for local
  development, and `CODEBREAKERS_LOG_LEVEL` sets the level. Uvicorn's access
  log is off. The correlation middleware writes one `request_completed` event
  per request instead: method, path, status, and duration, with no query
  string or body.
- **Context binding.** Correlation and job IDs are bound with
  `bind_log_context(...)`, a `ContextVar`, at the request and job boundaries.
  Every record inside the boundary carries the IDs, so call sites do not
  repeat them.
- **Redaction is defence in depth, not the primary control.** The primary
  control is that code never logs payloads; messages hold identifiers and
  outcomes only. The formatter also:
  - replaces any field named like `text`, `key`, `plaintext`, `ciphertext`,
    `password`, `token`, `secret`, `authorization`, or `database_url` with
    `[REDACTED]`;
  - masks credentials in URLs;
  - records exceptions by type and stack only, because exception messages can
    echo input.
- **Destinations.** Stdout reaches Log Analytics (`ContainerAppConsoleLogs_CL`).
  `codebreakers.*` loggers are also exported over OpenTelemetry to the
  Application Insights `traces` table, with the trace and span IDs attached.

### Traces

- **API:** FastAPI is auto-instrumented with server spans per route.
- **Database:** a small SQLAlchemy engine listener (`trace_engine`) emits
  client spans for each statement. Spans carry the operation, database name,
  server address, and parameterized SQL, never bound parameter values. The
  upstream `opentelemetry-instrumentation-sqlalchemy` package does not
  support SQLAlchemy 2.1 yet, so it is not used. Replacing the listener with
  it is a drop-in change when that support lands.
- **Messaging:**
  - Publishing an analysis job, through the outbox relay or Service Bus,
    creates a producer span.
  - The message carries W3C `traceparent` and the `correlation_id`.
  - The worker starts a consumer span as a child of that context, so one
    trace links the HTTP request, the relay, the queue, and the job.

### Metrics

- **API RED.** Rate, errors, and duration come from the request spans, as the
  Application Insights `requests` table (`resultCode`, `duration`).
- **Jobs.** The worker records these counters and histograms:
  - `codebreakers.jobs.processed` (by outcome);
  - `codebreakers.jobs.duration` (histogram, by outcome);
  - `codebreakers.jobs.retries`;
  - `codebreakers.jobs.dead_lettered` (by reason).
- **Queue.** The worker samples three gauges from PostgreSQL, at most once
  every 10 s:
  - `codebreakers.queue.depth`;
  - `codebreakers.queue.oldest_age` (seconds the oldest ready message has
    waited);
  - `codebreakers.queue.dead_letters`.
- **Known gaps.**
  - Gauges exist only while a worker replica runs. The worker scales to zero
    when the queue is empty, so a gap means "idle", not "no data". KEDA starts
    a worker as soon as anything is queued, which brings the gauges back.
  - If the worker crash-loops, the replica-restart alert catches it. Pending
    jobs with no replicas require checking the KEDA scale rule.
  - On the Service Bus backend, messages the broker dead-letters itself
    (`MaxDeliveryCountExceeded`) are not counted by the application. Use the
    namespace's `DeadletteredMessages` metric there.

### Correlation

The `X-Request-ID` header, or a generated UUID, becomes the correlation ID. It
is returned in the response header and in every problem body, stored on the
job, sent in the message, and bound in the worker's logs. Given an ID from a
user or an error response:

```kusto
// Application Insights → Logs
let id = "<correlation id>";
let operations = traces
    | where timestamp > ago(7d) and tostring(customDimensions.correlation_id) == id
    | distinct operation_Id;
union requests, dependencies, traces, exceptions
| where timestamp > ago(7d) and operation_Id in (operations)
| project timestamp, cloud_RoleName, itemType, name, message, resultCode, duration
| order by timestamp asc
```

Without OpenTelemetry, for example when the exporter is failing, the same
search works on the raw JSON logs:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(7d) and Log_s has "<correlation id>"
| project TimeGenerated, ContainerAppName_s, Log_s
| order by TimeGenerated asc
```

### Alerts and dashboard

Terraform (`infra/azure/observability.tf`) creates one action group, which
emails `budget_contact_emails`, and these alerts. Each alert's description
links to its runbook in [alerts.md](../operations/alerts.md).

| Alert | Source | Condition (15 min window) |
| --- | --- | --- |
| API server errors | Container Apps `Requests`, `statusCodeCategory = 5xx` | > 5 |
| Replica restarts (api, relay, worker) | Container Apps `RestartCount` (max) | > 2: covers crash loops and failed revisions |
| Dead letters | `codebreakers.jobs.dead_lettered` | > 0 |
| Old queued jobs | `codebreakers.queue.oldest_age` | > 300 s |
| PostgreSQL CPU / memory / storage / connections | Flexible Server metrics | > 80 % / 90 % / 80 % / `postgres_connection_alert_threshold` |

Platform metrics drive the HTTP and restart alerts, so they work even if the
application cannot export telemetry. An Application Insights workbook,
*Codebreakers service (env)*, shows:

- API requests by status;
- API latency percentiles;
- jobs processed by outcome;
- job duration;
- retries and dead letters;
- queue depth and age.

### What telemetry must never contain

Never: submitted text, results, cipher keys, database URLs, connection
strings, `Authorization` or `Cookie` headers, or exception messages.
`tests/test_telemetry_redaction.py` enforces this end to end. It runs the
instrumented app with sentinel values in every request field and header,
captures the logs and spans, and asserts that no sentinel appears anywhere.

## Consequences

- One correlation ID is enough to follow a request into its worker job.
- **Residual risk: unhandled exceptions.** FastAPI auto-instrumentation records
  the message of an *unhandled* exception (one that becomes a 500) on the
  request span. Expected failures are mapped to problem responses first and
  never reach it. Any message that does reach it is an incident to fix: see
  the [threat model](../security/threat-model.md).
- **Residual risk: `exc_info` exports.** The Azure Monitor log exporter would
  send `exception.message` for any application log call that used
  `exc_info`. No `codebreakers.*` logger does. Keep it that way.
- Gauges that are sampled by the worker trade some fidelity, with gaps at
  idle, for zero extra always-on processes.
- Telemetry volume is bounded by the Log Analytics daily cap (ADR-0012).
  Hitting the cap stops ingestion for the day, which also silences the
  telemetry-based alerts. The platform-metric alerts keep working.
