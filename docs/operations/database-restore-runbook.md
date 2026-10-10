# Runbook: Restore the Database

Recover PostgreSQL data from Azure Flexible Server's automatic backups. Use
it when data was damaged or deleted by mistake, for example by a faulty
migration or a bad bulk operation, or when the server is lost.

**What is at stake.** The database holds analysis jobs and results for 7 days
(`CODEBREAKERS_ANALYSIS_RETENTION_DAYS`), plus the queue and outbox. Source
text is deleted as soon as a job finishes. Often the cheapest recovery is to
let users resubmit. Restore when recent results or in-flight jobs matter, or
when the schema itself is damaged.

| | Target |
| --- | --- |
| **RPO** (data you can lose) | Minutes. Point-in-time restore can go to any moment within the backup retention (7 days in dev, 14 in prod). |
| **RTO** (time to recover) | About 1 hour: creating the restored server takes most of it, and copying this small database takes minutes. |

Backups are locally redundant and not geo-redundant (ADR-0012). A
whole-region outage is not covered.

## Overview

1. Restore the backup to a **new** server at the chosen point in time.
2. Check the data on the new server.
3. Pause the application and copy the data back into the Terraform-managed
   server. Terraform, the Key Vault `database-url` secret, and the apps stay
   unchanged.
4. Resume the application, verify, and delete the temporary server.

Copying back avoids repointing the apps at a server Terraform does not know
about.

## 0. Prepare

```bash
TF="terraform -chdir=infra/azure"
RG=$($TF output -raw resource_group_name)
PG=$($TF output -raw postgres_server_name)
KV=$($TF output -raw key_vault_name)
API_APP=$($TF output -json container_app_names | jq -r .api)
RESTORE_POINT="2026-01-01T12:00:00Z"   # UTC, just before the incident
TEMP_PG="$PG-restore-$(date -u +%m%d%H%M)"
MY_IP=$(curl -fsS https://api.ipify.org)
WORK=$(mktemp -d)   # the dump holds user submissions; keep it out of the repo
```

Read the admin password from Key Vault. Do not paste it into tickets or chat:

```bash
DB_URL=$(az keyvault secret show --vault-name "$KV" --name database-url --query value -o tsv)
export PGPASSWORD=$(python3 -c 'import sys, urllib.parse as u; print(u.unquote(u.urlsplit(sys.argv[1]).password))' "$DB_URL")
unset DB_URL
```

## 1. Restore to a new server

```bash
az postgres flexible-server restore -g "$RG" --name "$TEMP_PG" \
  --source-server "$PG" --restore-time "$RESTORE_POINT"
```

The new server gets the source's configuration and admin login. Allow your IP
on both servers for the copy. These rules are not managed by Terraform, so
remove them at the end:

```bash
for server in "$PG" "$TEMP_PG"; do
  az postgres flexible-server firewall-rule create -g "$RG" -n "$server" \
    --rule-name restore-operator --start-ip-address "$MY_IP" --end-ip-address "$MY_IP"
done
```

## 2. Check the restored data

Use client tools that match the server version (16). Docker avoids a local
install:

```bash
psql16() { docker run --rm -i -e PGPASSWORD postgres:16 psql "$@"; }
psql16 "host=$TEMP_PG.postgres.database.azure.com user=codebreakers dbname=codebreakers sslmode=require" \
  -c "SELECT status, count(*) FROM analysis_jobs GROUP BY status" \
  -c "SELECT version_num FROM alembic_version"
```

Confirm that the counts look right and that the Alembic revision matches the
code you will run. If the incident was a bad migration, restore to a point
before it and deploy code that matches that schema; see the
[rollback runbook](rollback-runbook.md#schema-problems).

## 3. Pause the application and copy the data back

Stop new work by denying all traffic at the API's ingress. Callers get
`403`. Revisions and traffic weights are not touched:

```bash
az containerapp ingress access-restriction set -g "$RG" -n "$API_APP" \
  --rule-name maintenance --ip-address 0.0.0.0/0 --action Deny
```

The relay and worker scale to zero about 5 minutes after the queue empties.
Wait until both report `0` replicas, so nothing writes during the copy:

```bash
for role in relay worker; do
  az containerapp replica list -g "$RG" -n "$($TF output -json container_app_names | jq -r .$role)" --query 'length(@)'
done
```

Dump the restored database and load it into the original. `--clean` replaces
every object, including `alembic_version`:

```bash
docker run --rm -e PGPASSWORD -v "$WORK:/work" postgres:16 pg_dump \
  "host=$TEMP_PG.postgres.database.azure.com user=codebreakers dbname=codebreakers sslmode=require" \
  --format=custom --file=/work/restore.dump
docker run --rm -e PGPASSWORD -v "$WORK:/work" postgres:16 pg_restore \
  --dbname="host=$PG.postgres.database.azure.com user=codebreakers dbname=codebreakers sslmode=require" \
  --clean --if-exists --no-owner --single-transaction /work/restore.dump
```

`--single-transaction` makes the load all or nothing. If it fails, the
original data is unchanged.

## 4. Resume and verify

```bash
az containerapp ingress access-restriction remove -g "$RG" -n "$API_APP" --rule-name maintenance
make azure-smoke
```

- `GET /v1/analyses/<id>` returns jobs that existed at the restore point.
- The relay picks up restored outbox rows, and pending jobs complete. Watch
  the workbook and the [queue-age alert](alerts.md#queue-age).
- **Data-deletion requests.** The restore brings back jobs that were deleted
  on request after the restore point. Delete them again using the job IDs
  from the request records ([SECURITY.md](../../SECURITY.md#data-deletion)).

## 5. Clean up

```bash
rm -rf "$WORK"
for server in "$PG" "$TEMP_PG"; do
  az postgres flexible-server firewall-rule delete -g "$RG" -n "$server" --rule-name restore-operator --yes
done
az postgres flexible-server delete -g "$RG" -n "$TEMP_PG" --yes
unset PGPASSWORD
```

The dump contains user submissions. Delete it as shown, and never copy it
anywhere else.

## If the original server is gone

A deleted server can only be deleted in `dev`, or in `prod` after its lock
is removed. Azure keeps its backups for a short time after deletion.

1. Restore the backup to a temporary server by following Azure's *Restore a
   dropped server* guide, instead of step 1.
2. Run `make tf-plan` and `make tf-apply`. These recreate an empty server, the
   database, and the firewall rule with the names Terraform expects.
3. Continue with steps 2–5.
4. Run `make azure-migrate` if the backup is older than the deployed schema.

## Drill

Once per phase, practise steps 0–2 in `dev`: restore to a temporary server,
check it, then delete it. It costs a few cents and proves that backups
actually restore.
