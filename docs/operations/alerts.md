# Runbook: Alerts

Terraform (`infra/azure/observability.tf`) defines the alerts below. All of
them notify one action group, `ag-codebreakers-<env>`, which emails
`budget_contact_emails`. Each alert's description links to its section here.
The design is in [ADR-0014](../architecture/ADR-0014-observability.md).

| Alert | Fires when (15 min window) | Severity |
| --- | --- | --- |
| [`alert-…-api-5xx`](#api-server-errors) | More than 5 API responses with status 5xx | 2 |
| [`alert-…-{api,relay,worker}-restarts`](#replica-restarts) | A replica restarted more than twice | 2 |
| [`alert-…-postgres-{cpu,memory,storage,connections}`](#database-saturation) | CPU > 80 %, memory > 90 %, storage > 80 %, or connections above `postgres_connection_alert_threshold` | 2 (storage: 1) |
| [`alert-…-dead-letters`](#dead-letters) | Any analysis job was dead-lettered | 2 |
| [`alert-…-queue-age`](#queue-age) | The oldest ready job has waited more than 5 minutes | 2 |

## First steps for any alert

Set up the shell variables used throughout this runbook:

```bash
TF="terraform -chdir=infra/azure"
RG=$($TF output -raw resource_group_name)
API=$($TF output -raw api_url)
eval "$($TF output -json container_app_names | jq -r 'to_entries[] | "APP_\(.key|ascii_upcase)=\(.value)"')"
# Defines APP_API, APP_RELAY, and APP_WORKER.
```

1. Open the Application Insights workbook **Codebreakers service (<env>)**.
   It shows requests by status, latency, jobs by outcome, retries and dead
   letters, and queue depth and age on one page.
2. Check whether a deployment happened just before the alert. If the timing
   matches, [roll back](rollback-runbook.md) first and investigate after:

   ```bash
   az containerapp revision list -g "$RG" -n "$APP_API" \
     --query "[].{name:name, created:properties.createdTime, traffic:properties.trafficWeight, active:properties.active}" -o table
   ```

3. Follow a single failing request or job across services with the
   correlation query in
   [ADR-0014](../architecture/ADR-0014-observability.md#correlation).

Alerts resolve on their own once the condition clears. To check that email
delivery works at all, open **Monitor → Alerts → Action groups**, select the
group, and choose **Test action group**.

The **Test** subsections below fire each alert on purpose. Run them in `dev`
only, and restore the environment when you are done.

## api-server-errors

**Meaning.** The API returned more than five 5xx responses within 15 minutes.
The count comes from the Container Apps ingress, so it includes responses the
application never sent: `502`/`503` when no healthy replica exists, and
`503 analysis-capacity-exceeded` when the unfinished-job cap is reached.

**Triage.**

```kusto
// Application Insights → Logs
requests
| where timestamp > ago(1h) and toint(resultCode) >= 500
| summarize count() by name, resultCode, cloud_RoleInstance, bin(timestamp, 5m)
```

```kusto
exceptions
| where timestamp > ago(1h)
| summarize count() by type, outermostMethod
```

Common causes:

- **Database unavailable.** `curl "$API/health/ready"` reports
  `"database": "failing"`. The server may be stopped: compare
  `az postgres flexible-server show -g "$RG" -n "$($TF output -raw postgres_server_name)" --query state`
  with [database saturation](#database-saturation).
- **Capacity cap.** Problem type `analysis-capacity-exceeded`. Look at
  [queue age](#queue-age): the worker is not keeping up, or not running.
- **Bad release.** Errors started with a new revision. [Roll back](rollback-runbook.md).

**Test (dev).** Stop the database, send requests, then start it again:

```bash
PG=$($TF output -raw postgres_server_name)
az postgres flexible-server stop -g "$RG" -n "$PG"
for i in $(seq 10); do curl -s -o /dev/null -w '%{http_code}\n' "$API/health/ready"; done
# Expect 503s. The email arrives within about 20 minutes.
az postgres flexible-server start -g "$RG" -n "$PG"
```

## replica-restarts

**Meaning.** A container in the API, relay, or worker restarted more than
twice. `RestartCount` is cumulative per replica, so one restart is tolerated
but a crash loop is not. Usual causes:

- a configuration error at startup (the worker and relay exit with code `2`);
- an unreadable Key Vault secret reference;
- running out of memory;
- failed API liveness probes.

**Triage.**

```bash
az containerapp logs show -g "$RG" -n "$APP_WORKER" --type system --tail 50 --follow false
az containerapp logs show -g "$RG" -n "$APP_WORKER" --type console --tail 50 --follow false
```

```kusto
// Log Analytics workspace
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(1h) and ContainerAppName_s startswith "ca-codebreakers"
| project TimeGenerated, ContainerAppName_s, RevisionName_s, Reason_s, Log_s
| order by TimeGenerated desc
```

**Actions.**

- **New revision:** [roll back](rollback-runbook.md).
- **Configuration error:** fix it in Terraform and apply.
- **OOMKilled:** check `CODEBREAKERS_ANALYSIS_MEMORY_LIMIT_MB` against the
  container's memory.

**Test (dev).** Give the worker an invalid setting and keep one replica
running, so it crash-loops:

```bash
az containerapp update -g "$RG" -n "$APP_WORKER" --min-replicas 1 \
  --set-env-vars CODEBREAKERS_ANALYSIS_TIME_BUDGET_SECONDS=invalid
# The email arrives within about 20 minutes. Then let Terraform restore the
# environment variables and replica count (the plan changes only the worker):
make tf-plan
make tf-apply
```

## database-saturation

**Meaning.** A PostgreSQL Flexible Server metric crossed its threshold:

| Metric | Threshold | What it means |
| --- | --- | --- |
| `cpu_percent` | > 80 % average | Slow queries, or more work than the SKU can handle. |
| `memory_percent` | > 90 % average | Too many connections or large sorts. |
| `storage_percent` | > 80 % maximum | **Severity 1.** The server becomes read-only when the disk is full. |
| `active_connections` | > `postgres_connection_alert_threshold` (40) | Close to the limit (`B_Standard_B1ms` allows 50). |

**Triage.** Open the server in the portal: **Monitoring → Metrics** for the
trend, and **Intelligent Performance → Query Performance Insight** for the
most expensive queries. Then check each case:

- **Connections.** Count the replicas: each API, relay, and worker process
  holds a small pool.

  ```bash
  az containerapp replica list -g "$RG" -n "$APP_API" --query 'length(@)'
  ```

- **Storage.** Is retention running?

  ```bash
  az containerapp job execution list -g "$RG" \
    -n "$($TF output -json container_app_job_names | jq -r .retention)" -o table
  ```

**Actions.**

- Run the retention job by hand (`scripts/run_azure_job.sh "$RG" <job>`).
- Lower the replica maximums (`api_replicas`, `worker_replicas`).
- Raise `postgres_sku_name` or `postgres_storage_mb` in the environment's
  `.tfvars` and apply. Storage can grow but never shrink.

**Test (dev).** Lower the connection threshold to zero. Azure always holds
some connections of its own:

```bash
TF_VAR_postgres_connection_alert_threshold=0 make tf-plan   # only the alert changes
make tf-apply
# The email arrives within about 20 minutes. Then restore the threshold:
make tf-plan
make tf-apply
```

## dead-letters

**Meaning.** The worker dead-lettered at least one job. The reason is either
`invalid-message` (the body could not be decoded) or `retries-exhausted`
(transient failures on every attempt). The job will not finish unless someone
acts.

**Triage.**

```kusto
// Application Insights → Logs
customMetrics
| where timestamp > ago(1d) and name == "codebreakers.jobs.dead_lettered"
| extend reason = tostring(customDimensions["codebreakers.dead_letter.reason"])
| summarize count = sum(valueSum) by reason, bin(timestamp, 15m)
```

```kusto
traces
| where timestamp > ago(1d) and message has_any ("job_dead_lettered", "job_message_rejected")
| project timestamp, message, job_id = tostring(customDimensions.job_id),
          correlation_id = tostring(customDimensions.correlation_id)
```

**Actions.** Follow the [dead-letter runbook](dead-letter-runbook.md): list
the messages, fix the cause, then replay or discard each one. The
`codebreakers.queue.dead_letters` gauge in the workbook shows how many are
still waiting.

**Test (dev).** Put a message that cannot be decoded on the queue. Open a shell
in the API app. It has the worker's image and database settings, and it lets
you use quotes:

```bash
curl -fsS "$API/health/live" > /dev/null   # wake a replica
az containerapp exec -g "$RG" -n "$APP_API" --command sh
```

In the container:

```sh
python - <<'EOF'
import os
from sqlalchemy import text
from codebreakers.infrastructure.persistence.postgres import create_postgres_engine
engine = create_postgres_engine(os.environ["CODEBREAKERS_DATABASE_URL"])
with engine.begin() as connection:
    connection.execute(text(
        "INSERT INTO analysis_job_queue (payload, enqueued_at, available_at)"
        " VALUES ('alert-test', now(), now())"
    ))
EOF
```

KEDA starts a worker, which dead-letters the message as `invalid-message`.
The email arrives within about 30 minutes. Then remove the test message:

```sh
codebreakers deadletter list                      # note its sequence_number
codebreakers deadletter discard --sequence-number <n>
```

## queue-age

**Meaning.** The oldest ready job has waited more than 5 minutes. Jobs are
arriving faster than the workers finish them, or workers are running but not
consuming. This is the queue-backlog runbook.

The gauge is reported by running workers, so there is no data when the worker
has scaled to zero. A worker that cannot start at all shows up as
[replica restarts](#replica-restarts) instead. Jobs that stay `pending` with
no worker replicas point to a broken KEDA scale rule. See *Jobs stay pending*
in the [Azure runbook](azure-runbook.md#troubleshooting).

**Triage.**

```kusto
// Application Insights → Logs
customMetrics
| where timestamp > ago(6h) and name in ("codebreakers.queue.depth", "codebreakers.queue.oldest_age")
| summarize max(valueMax) by name, bin(timestamp, 5m)
| render timechart
```

```bash
az containerapp replica list -g "$RG" -n "$APP_WORKER" --query 'length(@)'
az containerapp logs show -g "$RG" -n "$APP_WORKER" --type console --tail 50 --follow false
```

- **Depth rising and workers at `worker_replicas.max`:** demand is above
  capacity. Raise `worker_replicas.max`. The unfinished-job cap
  (`CODEBREAKERS_MAX_PENDING_ANALYSES`) returns `503` to new submissions until
  the backlog drains.
- **Workers running but `jobs.processed` flat:** the workers may be stuck on
  the database (see [database saturation](#database-saturation)) or crashing
  mid-job. Look for `job_retry_scheduled` in the logs.
- **One old message, otherwise healthy:** a message is held by a lease that
  has not expired. It is redelivered when the lease lapses (time budget plus
  margin).

**Test (dev).** Keep a worker running and add a ready message that is locked
and backdated, so no worker can take it:

```bash
az containerapp update -g "$RG" -n "$APP_WORKER" --min-replicas 1
az containerapp exec -g "$RG" -n "$APP_API" --command sh
```

In the container, run the Python snippet from [dead-letters](#dead-letters)
with this statement instead:

```sql
INSERT INTO analysis_job_queue
  (payload, enqueued_at, available_at, lock_token, locked_until)
VALUES ('alert-test', now(), now() - interval '10 minutes',
        gen_random_uuid(), now() + interval '30 minutes')
```

The email arrives within about 30 minutes. When the lock lapses, the worker
dead-letters the message, which also fires [dead-letters](#dead-letters).
Discard it as described there, then restore the replica minimum with
`make tf-plan` and `make tf-apply`.
