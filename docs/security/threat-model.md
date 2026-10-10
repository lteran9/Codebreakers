# Threat Model

Scope: the public Codebreakers API, its worker pipeline, the Azure environment
([ADR-0012](../architecture/ADR-0012-azure-dev-environment.md)), and the
delivery pipeline ([ADR-0013](../architecture/ADR-0013-continuous-delivery.md)).
Reviewed for Phase 9. Revisit when accounts, new data stores, or new public
endpoints are added.

## The ciphers protect nothing

Caesar, Vigenère, and the other ciphers here are **broken classical ciphers.
They offer zero confidentiality.** The analyzers in this project exist to
break them in seconds, and so do countless other tools. Never use them to
protect passwords, personal data, or any real secret. Keys are not secrets
either: a key travels in the same request as the text, and it can be
recovered from the ciphertext.

The controls below therefore do not defend key secrecy, which would be
security theatre. They protect two things that matter:

- the **privacy of what people submit**, which may be a real historical
  letter, a diary, or a family document;
- the **availability and cost** of a public service.

## Assets

| Asset | Why it matters | Where it lives |
| --- | --- | --- |
| Submitted text and analysis results | May be private personal or historical material. Results contain the recovered plaintext. | PostgreSQL. Source text is deleted when the job finishes, and results are deleted after `CODEBREAKERS_ANALYSIS_RETENTION_DAYS` (7). Backups keep both for the backup window (7 days dev, 14 days prod). |
| Job identifiers | Anyone holding one can read that job's result. | Returned to the submitter, and stored in logs and telemetry. |
| Service availability and spend | A public endpoint can be driven into cost or outage. | Container Apps, PostgreSQL, budget. |
| Database credential | Grants full access to all stored jobs. | Key Vault secret, read by the app identity only. |
| Delivery identity | Can change what runs in Azure. | GitHub environment (OIDC); no stored secret. |
| Build inputs | Dependencies, base image, and actions decide what code runs. | Lock files, Dockerfile digest, workflow pins. |

## Trust boundaries

1. Internet → API ingress (TLS terminated by Container Apps / Envoy).
2. API, relay, and worker → PostgreSQL (TLS + password; firewall admits
   Azure-hosted sources only).
3. Queue → worker (messages are re-validated; the worker trusts only the
   database).
4. GitHub Actions → Azure (OIDC federated credential bound to one GitHub
   environment).
5. Upstream packages, images, and actions → build.

## Threats and controls

### Untrusted text

**Threat:** crafted input that crashes, hangs, or exhausts a process, such as
huge bodies, unusual Unicode, or inputs that drive analyzers into worst-case
behaviour.

**Controls:**

- The API limits request bodies to 64 KiB, text to 20,000 characters, and keys
  to 4,096 characters.
- Text is treated as Unicode code points under explicit rules (ADR-0004), and
  characters outside the alphabet pass through untouched.
- Analysis never runs in the request. Each job runs in a child process with
  a wall-clock budget (30 s) and a memory budget, and is cancelled when it
  exceeds either.
- Input is never evaluated or used to build SQL or shell commands; SQL is
  parameterized.
- Errors return RFC 9457 problems that do not echo the submitted text.
- Client request IDs must match `[A-Za-z0-9._-]{1,128}`, or a new one is
  generated, which prevents log injection.

**Residual risk:** a novel analyzer bug may still waste one job's budget.
The cap on unfinished jobs bounds how many such jobs can run at once.

### Denial of service and cost exhaustion

**Threat:** floods of cheap requests, floods of expensive analysis
submissions, or a filled queue that starves other users and raises the bill.

**Controls:**

- **Rate limits.** A token bucket per client IP allows 120 requests a minute
  in general and 10 analysis submissions a minute, returning `429` with
  `Retry-After`. Health probes are exempt. The client IP is the last
  `X-Forwarded-For` hop added by the Container Apps proxy
  (`CODEBREAKERS_TRUSTED_PROXY_HOPS=1`). Clients cannot spoof it by sending
  their own header, because only the proxy's hop is trusted.
- **Workload cap.** At most `CODEBREAKERS_MAX_PENDING_ANALYSES` (100)
  unfinished jobs. Beyond that, submissions get `503` with `Retry-After`
  instead of queueing without bound.
- **Scale limits.** API and worker replica maxima bound compute spend. The
  budget alerts at 80 % and 100 % (ADR-0012).
- **Alerts.** 5xx, restart, queue-age, and database-saturation alerts surface
  sustained attacks ([alerts](../operations/alerts.md)).

**Residual risk, accepted for a portfolio service:**

- **Limits are per replica.** Each API replica keeps its own buckets in
  memory, so the effective limit is the configured value times the number of
  running replicas (at most 2 in dev, 5 in prod).
- **Distributed clients.** Many source IPs each get their own allowance, and
  enough of them can fill the job cap, which degrades analysis for everyone
  (but not the synchronous cipher endpoints).
- **Upgrade path.** A shared limiter, for example in Redis, or an edge
  limiter (Azure Front Door WAF rate-limit rules or API Management). Front
  Door would also add bot and geo filtering.

### Sensitive historical material

**Threat:** someone submits a real private document, and it leaks through
storage, enumeration, logs, telemetry, or backups.

**Controls:**

- **Data minimisation.** Source text is deleted as soon as the job finishes.
  Results are purged by the daily retention job after 7 days.
- **No enumeration.** `GET /v1/analyses` returns `404` in Azure
  (`CODEBREAKERS_ANALYSIS_LISTING_ENABLED=false`), so results are reachable
  only by their random UUIDv4 (122 bits). The job ID works like a bearer
  link: share it only with people who should see the result.
- **No content in telemetry.** Logs, traces, and metrics carry only
  identifiers, outcomes, and timings, enforced end to end by
  `tests/test_telemetry_redaction.py` ([ADR-0014](../architecture/ADR-0014-observability.md)).
- **Encryption.** TLS in transit to the API and the database. Azure encrypts
  data at rest.
- **Deletion on request.** See [SECURITY.md](../../SECURITY.md#data-deletion).

**Residual risk:**

- Anyone holding a job ID can read the result until it expires.
- Deleted data persists in PostgreSQL backups until the backup window passes.
- An *unhandled* exception (one that becomes a 500) could include input in
  its message, and FastAPI auto-instrumentation records that message on the
  request span. Expected failures are mapped to problem responses and never
  reach it. Treat any exception message seen in Application Insights as an
  incident: fix the mapping, then purge the data
  ([credential and data exposure runbook](../operations/credential-compromise-runbook.md)).

### Dependency and supply-chain compromise

**Threat:** a malicious or vulnerable package, base image, GitHub Action, or
build step ships into production.

**Controls:**

- Runtime dependencies are installed with `--require-hashes` from
  `requirements/runtime.txt`.
- The base image is pinned by digest.
- The runtime image has no pip or compilers and runs as a non-root user.
- Every GitHub Action is pinned to a commit SHA. Workflows default to
  read-only `contents` permission and grant `id-token` only to deploy jobs.
- Dependabot proposes updates after a **7-day cooldown**, which lets the
  ecosystem catch compromised releases first. The updates are grouped, and
  each must pass every CI gate. The PostgreSQL 16 → 18 Compose bump showed
  why the gates matter: it passed review but broke the local stack until
  Phase 9 added the Compose smoke test to CI.
- Trivy scans the locked dependencies, the source (for secrets), and the
  built image (vulnerabilities and secrets). Checkov scans the Terraform.
- Each release publishes a CycloneDX SBOM, and its image carries signed SLSA
  build provenance in ACR. Release assets are attested too.

**Residual risk:**

- A compromised release that stays undetected past the cooldown and the
  scanners.
- A compromised maintainer GitHub account. Mitigations: require 2FA, protect
  `master`, and require reviewers on `production`.

### Queue tampering

**Threat:** forged, replayed, or modified job messages cause wrong results,
data access, or worker crashes.

**Controls:**

- The PostgreSQL queue lives in the application database. Writing to it
  requires the database credential, which is TLS-only and held in Key Vault.
- Messages carry identifiers only. The worker loads the job and its text
  from the database, so a forged message can at most re-trigger an existing
  job.
- Messages are versioned (`schema_version`) and validated. Invalid messages
  go to the dead-letter queue instead of being retried.
- The worker is idempotent: a finished job is never re-run.
- Dead-letter replay re-validates each message and resets its attempt
  counter.
- On Service Bus, publishing requires the namespace's credentials or RBAC.

**Residual risk:**

- The database firewall's "allow Azure services" rule admits connections
  from any Azure-hosted source, including other tenants. The password and
  TLS are then the only barrier.
- The apps connect as the server administrator.
- Future hardening: VNet integration with private access, Microsoft Entra
  authentication for PostgreSQL, and separate least-privilege roles for the
  API, the worker, and migrations.

### Unauthorized data access

**Threat:** reading or changing other people's jobs or the infrastructure.

**Controls:**

- No HTTP endpoint lists, modifies, or deletes other jobs in Azure.
- Operations such as dead-letter handling and deletion run through Azure
  RBAC (`az containerapp exec`), not public endpoints.
- The app identity can pull images, publish telemetry, and read exactly one
  secret.
- The delivery identity is scoped to one resource group and its registry,
  and it is usable only from the matching GitHub environment.
- Key Vault uses RBAC, and Application Insights accepts only Entra-
  authenticated ingestion.

**No API authentication, by design.** The service has no accounts and no
saved jobs. Adding authentication now would only gate an anonymous,
throwaway tool. When user-specific saved jobs are introduced:

1. Authenticate with **Microsoft Entra ID** (or Entra External ID for public
   users) using OAuth 2.0 / OpenID Connect bearer tokens. Do not build a
   custom password system.
2. Authorize by **resource ownership**:
   - store the owner's subject (`oid`/`sub`) on each job;
   - filter every query by it;
   - return `404`, not `403`, for jobs the caller does not own, so their
     existence is not revealed;
   - test cross-user access explicitly.
3. Re-enable listing only as "my jobs".

## Delivery pipeline

**Threat:** an attacker who can run workflows deploys their own code.

**Controls:**

- The federated credential trusts only
  `repo:<owner>/<repo>:environment:<name>`.
- Pull requests never run in a deployment environment.
- `production` requires reviewer approval.
- Deployments only ship images that passed the full CI run in the same
  workflow.
- A failed smoke test leaves traffic on the previous revision.

**Residual risk:** anyone who can push to `master` can deploy to `dev`. Enable
branch protection with required reviews and status checks.

## Response

Vulnerability reporting, data deletion, and incident handling are described
in [SECURITY.md](../../SECURITY.md) and the
[operations runbooks](../operations/alerts.md).
