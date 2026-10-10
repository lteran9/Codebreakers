# Runbook: Analysis Job Dead Letters

Analysis job messages are dead-lettered when they can never succeed by
retrying. Use this runbook to inspect them, then replay or discard each one.
Background on retries and leases is in
[ADR-0009](../architecture/ADR-0009-async-analysis-worker.md).

## How you find out

- **Alert.** In Azure, the `alert-codebreakers-<env>-dead-letters` alert emails
  the operators when the worker dead-letters any job
  ([alerts runbook](alerts.md#dead-letters)).
- **Metrics.**
  - `codebreakers.jobs.dead_lettered` counts dead-lettered jobs by
    `codebreakers.dead_letter.reason`.
  - `codebreakers.queue.dead_letters` is the number still waiting (PostgreSQL
    backend).
  - Both appear in the *Codebreakers service* workbook
    ([ADR-0014](../architecture/ADR-0014-observability.md#metrics)).
- **Logs.** Look for `job_dead_lettered` (retries exhausted) and
  `job_message_rejected` (invalid message) events.

On Service Bus, the broker can also dead-letter messages itself
(`MaxDeliveryCountExceeded`). The application does not count those. Watch the
namespace's `DeadletteredMessages` metric instead.

## Why messages are dead-lettered

| `reason` | Meaning | Usual action |
| --- | --- | --- |
| `invalid-message` | Body is not a supported `AnalysisJobMessage` (bad JSON, unknown `schema_version`). | Fix the producer, then **discard**. Replay is refused. |
| `retries-exhausted` | Transient failures (for example the database or the child process) on every attempt. The `description` holds the last exception type. | Fix the cause, check the job status, then **replay** or **discard**. |
| `MaxDeliveryCountExceeded` | The broker gave up after repeated abandons or lock losses, usually workers crashing mid-message. | Investigate worker health, then **replay**. |
| `TTLExpiredException` | The message expired before a worker received it (Service Bus only, and only if the queue dead-letters on expiry). | **Replay** when workers are healthy. |

Dead-letter entries hold only identifiers and metadata. Job source text stays
in PostgreSQL and is never printed by these tools.

## Prerequisites

The commands use the same backend as the worker.

Azure Service Bus (`CODEBREAKERS_ANALYSIS_QUEUE=service-bus`):

```bash
export CODEBREAKERS_ANALYSIS_QUEUE=service-bus
export CODEBREAKERS_SERVICEBUS_CONNECTION_STRING='…'   # never commit or log it
export CODEBREAKERS_SERVICEBUS_QUEUE=analysis-jobs     # default
```

PostgreSQL queue (the local Docker Compose stack,
[ADR-0010](../architecture/ADR-0010-containers-local-stack.md)): run the
commands in a one-off container that already has the stack's configuration:

```bash
docker compose run --rm --no-deps worker deadletter list
```

PostgreSQL queue in Azure `dev`
([ADR-0012](../architecture/ADR-0012-azure-dev-environment.md)): the database
accepts only Azure traffic, so run the commands inside the API container app.
See the [Azure runbook](azure-runbook.md#operating).

## Inspect

```bash
codebreakers deadletter list --max 50
```

Each line is JSON:
`sequence_number`, `enqueued_at`, `delivery_count`, `reason`, `description`,
`job_id`, and `attempt`. `job_id` and `attempt` are `null` when the body
cannot be decoded. `list` only peeks, so messages are not locked or removed.
On the PostgreSQL backend, `sequence_number` is the queue row's `id`.

Then check the job each message refers to:

```bash
curl -s https://<api>/v1/analyses/<job_id>
```

- `succeeded`, `failed`, or `cancelled`: the job is finished, and replaying is
  a no-op. Discard the message.
- `pending` or `running`: the job can still be recovered. Replay it once the
  cause is fixed.
- `404`: retention purged the job. Discard the message.

## Replay

```bash
codebreakers deadletter replay --sequence-number 42 --sequence-number 43
codebreakers deadletter replay --all --max 200
```

Replay republishes each selected message to the main queue as **attempt 1**,
which restarts the retry budget and keeps the original correlation and trace
IDs. It then removes the message from the dead-letter queue. A message that
cannot be decoded is abandoned and counted as `skipped`. It stays in the
dead-letter queue until it is discarded.

The worker is idempotent, so replaying a finished job does no harm. A
`running` job whose lease has not lapsed is rescheduled until the lease
expires.

## Discard

```bash
codebreakers deadletter discard --sequence-number 42
codebreakers deadletter discard --all
```

Discarding removes messages permanently. A job that is still `pending` or
`running` stays that way until retention removes it. Replay the message
instead if the result is still wanted.

## Exit codes

`0` success, `2` missing or invalid configuration or selection (pass exactly
one of `--sequence-number …` or `--all`), `1` unexpected errors such as
network failures.

## Without the CLI

You can also use Service Bus Explorer in the Azure portal: open the queue,
choose **Dead-letter**, then **Peek** to inspect messages or **Receive** to
remove them. Prefer the CLI for replay. It restarts the attempt counter and
validates the schema, which a manual re-send does not.

On the PostgreSQL backend, dead letters are the rows of `analysis_job_queue`
where `dead_lettered_at IS NOT NULL`. Read them with SQL if needed, but
replay and discard through the CLI, for the same reasons.
