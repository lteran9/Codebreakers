# Security Policy

> **Codebreakers ciphers provide zero confidentiality.** Caesar, Vigenère, and
> the other classical ciphers here are broken by design. The project exists to
> break them. Never use them to protect real secrets. See the
> [threat model](docs/security/threat-model.md).

## Supported versions

Only the latest release and the current `master` branch receive fixes.

## Reporting a vulnerability

Please report vulnerabilities privately, not in public issues:

1. Open the repository's **Security** tab and choose **Report a
   vulnerability**. This uses GitHub private vulnerability reporting, which
   the maintainer enables in **Settings → Code security**.
2. Include the affected version or commit, the steps to reproduce, and the
   impact you observed. Do not include real personal data; use synthetic
   text.

Examples that are in scope:

- reading another person's analysis without its job ID;
- bypassing the rate or workload limits;
- submitted content appearing in logs or telemetry;
- injection;
- supply-chain or CI weaknesses.

The weakness of the classical ciphers themselves is not a vulnerability.

## Response process

| Step | Target |
| --- | --- |
| Acknowledge the report | 5 business days |
| Triage: confirm, rate severity (CVSS), identify affected releases | 10 business days |
| Fix, test, and release (`v*` tag; the release workflow deploys after approval) | Critical/high: 14 days; others: next release |
| Publish a GitHub Security Advisory crediting the reporter (unless they decline) | With the fix |

While a fix is in progress, the maintainer can contain the issue:

- [roll back](docs/operations/rollback-runbook.md) to a safe revision;
- tighten `CODEBREAKERS_RATE_LIMIT_PER_MINUTE` or
  `CODEBREAKERS_MAX_PENDING_ANALYSES` in Terraform;
- for a credential exposure, follow the
  [credential compromise runbook](docs/operations/credential-compromise-runbook.md).

Vulnerable dependencies reported by Dependabot or Trivy follow the same
targets. A finding may be accepted in `.trivyignore` only with a reason, a
reviewer, and a revisit date.

## Data deletion

The hosted service stores as little as possible
([ADR-0008](docs/architecture/ADR-0008-analysis-persistence.md)):

- **Source text** is deleted when its analysis finishes.
- **Analysis results** are deleted automatically after 7 days
  (`CODEBREAKERS_ANALYSIS_RETENTION_DAYS`).
- **Logs and telemetry** never contain submitted text or results. They hold
  job and correlation IDs, which Log Analytics keeps for 30 days.
- **Database backups** contain whatever existed during the backup window
  (7 days in dev, 14 in prod), and they expire on their own. Individual rows
  cannot be removed from a backup.

To have an analysis deleted sooner, send its job ID through the private
reporting channel above, or contact the maintainer. The job ID is the only
identifier, because there are no accounts. The operator then deletes the job,
its input, and any pending outbox entry:

```bash
TF="terraform -chdir=infra/azure"
RG=$($TF output -raw resource_group_name)
APP=$($TF output -json container_app_names | jq -r .api)
curl -fsS "$($TF output -raw api_url)/health/live" > /dev/null  # wake a replica
az containerapp exec -g "$RG" -n "$APP" --command \
  "python -m codebreakers.infrastructure.persistence.retention --delete-job <job-id>"
```

The command prints `Deleted 1 of 1 requested job(s).` A later
`GET /v1/analyses/<job-id>` returns `404`. Confirm the deletion to the
requester and note that backups age out within the backup window.

Locally (Docker Compose):

```bash
docker compose run --rm --no-deps --entrypoint python worker \
  -m codebreakers.infrastructure.persistence.retention --delete-job <job-id>
```
