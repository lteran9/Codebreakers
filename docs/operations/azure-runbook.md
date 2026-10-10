# Runbook: Azure Environment

Deploy, verify, operate, and tear down the Azure environment described in
[ADR-0012](../architecture/ADR-0012-azure-dev-environment.md). Terraform
conventions are in [ADR-0011](../architecture/ADR-0011-terraform.md). The
commands use `dev`. For `prod`, add `TF_ENV=prod` to every `make` command and
use `environments/prod.tfvars`.

Responsibilities are split
([ADR-0013](../architecture/ADR-0013-continuous-delivery.md)):

- **Terraform**, run by an operator, owns the infrastructure.
- **GitHub Actions** owns application releases: images, revisions, and API
  traffic.

Terraform ignores the image and traffic fields that CI changes. After the
[first deployment](#3-first-deployment) and
[connecting GitHub Actions](#4-connect-github-actions), every push to
`master` deploys `dev`.

## Prerequisites

- An Azure subscription where you hold **Owner**, or **Contributor** plus
  **User Access Administrator**, because Terraform creates role assignments.
- Azure CLI 2.60+ with the extension: `az extension add --name containerapp --upgrade`.
- Terraform 1.16+ and Python 3.12+ (for the smoke test).
- Docker Desktop running, with Docker Buildx available (`docker buildx version`),
  to build and push images locally without ACR Tasks.
- Resource providers registered once per subscription:

  ```bash
  for ns in Microsoft.App Microsoft.ManagedIdentity Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL \
            Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Insights \
            Microsoft.Storage Microsoft.Consumption; do
    az provider register --namespace "$ns" --wait
  done
  ```

Sign in, then point Terraform at the subscription:

```bash
az login
az account set --subscription "<subscription name or id>"
export ARM_SUBSCRIPTION_ID=$(az account show --query id --output tsv)
```

## 1. Bootstrap remote state (once per subscription)

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply
terraform -chdir=infra/bootstrap output -raw storage_account_name
```

- Keep the storage account name. Every later `tf-init` needs it as
  `TF_STATE_ACCOUNT`.
- `infra/bootstrap/terraform.tfstate` stays local and git-ignored. Keep a copy
  somewhere private. If it is lost, re-import the resources rather than
  re-running the bootstrap.
- The account has a delete lock. Leave it in place.

## 2. Configure local inputs

Budget alert recipients are personal, so they live in a git-ignored file:

```bash
cat > infra/azure/local.auto.tfvars <<'EOF'
budget_contact_emails = ["you@example.com"]
EOF
```

Then initialise the backend:

```bash
export TF_STATE_ACCOUNT=<bootstrap storage_account_name>
make tf-init TF_STATE_ACCOUNT=$TF_STATE_ACCOUNT
```

## 3. First deployment

The container apps need an image in the registry before they can start, and
the registry is part of the same configuration. The first deployment therefore
creates the registry on its own, builds the image, and then applies everything.

1. Commit your work. The image tag is the Git SHA. Check `git status --short`
   and commit intended source changes before building: a local Docker build
   does not enforce a clean tree.
2. Create only the registry and its prerequisites:

   ```bash
   terraform -chdir=infra/azure apply -var-file=environments/dev.tfvars \
     -var image_tag=$(git rev-parse HEAD) -target=azurerm_container_registry.main
   ```

   Terraform warns that `-target` is for exceptional use. This is that case,
   and the next step applies the full configuration.
3. [Build and push the image locally](#build-and-push-the-image-locally).

4. Plan, review, and apply. `tf-apply` applies only the saved plan:

   ```bash
   make tf-plan
   make tf-apply
   ```

   Review that the plan creates, and does not destroy, resources. It should
   contain three container apps, two jobs, PostgreSQL, Key Vault, and the
   budget. The apply waits about 60 s for role assignments to propagate, and
   the PostgreSQL server takes 5–10 minutes.
5. Run migrations, then smoke-test the public API:

   ```bash
   make azure-migrate
   make azure-smoke
   ```

   `azure-smoke` runs the Phase 7 smoke test against the HTTPS URL with a
   300 s timeout. The timeout allows for cold starts, because the relay and
   worker scale from zero (see step 5 of [Verify](#verify)).

## Build and push the image locally

Run from the repository root, after the registry has been provisioned. This
approach does not use ACR Tasks and works on Apple Silicon by targeting the
`linux/amd64` platform used by Container Apps.

```bash
ACR=$(terraform -chdir=infra/azure output -raw container_registry_name)
ACR_HOST=$(terraform -chdir=infra/azure output -raw container_registry_login_server)

az acr login --name "$ACR"

docker buildx build \
  --platform linux/amd64 \
  --tag "$ACR_HOST/codebreakers:$(git rev-parse HEAD)" \
  --push .
```

Continue only after the push succeeds. Keep the same commit checked out for
the following Terraform plan so the deployed image tag matches the pushed
image. After the first deployment, CI pushes the images instead. Use these
commands again only for a [break-glass release](#break-glass-release-from-a-workstation).

`make azure-image` remains an optional alternative for subscriptions that
support ACR Tasks; it builds inside Azure rather than locally. Do not use it
when ACR Tasks are unavailable. Neither approach requires registry admin
credentials: the local push uses your signed-in Azure identity.

## 4. Connect GitHub Actions

The CI workflow deploys through the GitHub environment `dev`, and the release
workflow deploys through `production`. Each environment signs in to Azure
with its own OIDC identity, `id-codebreakers-<env>-deploy`, which Terraform
creates. No client secret exists.

1. **Create the environments.** In **Settings → Environments**, create `dev`
   and `production`:
   - `dev`: under **Deployment branches and tags**, allow only `master`.
   - `production`: add yourself, or the release approvers, as **Required
     reviewers**, and allow only tags matching `v*`.

   Only workflow jobs that run in an environment can use its Azure identity,
   so these rules decide who can deploy.
2. **Set the environment variables** from Terraform. They are identifiers,
   not secrets:

   ```bash
   gh api --method PUT "repos/{owner}/{repo}/environments/dev" > /dev/null   # if not created above
   terraform -chdir=infra/azure output -json github_environment_variables |
     jq -r 'to_entries[] | "\(.key)\t\(.value)"' |
     while IFS=$'\t' read -r name value; do gh variable set "$name" --env dev --body "$value"; done
   gh variable list --env dev
   ```

   Repeat with `TF_ENV=prod` outputs and `--env production` once `prod` is
   provisioned. Until then, the production job passes its approval gate and
   then skips with a notice.
3. **Protect `master`.** Go to **Settings → Rules → Rulesets** (or **Branches
   → Branch protection rules**).
   - Require a pull request before merging.
   - Require these status checks to pass:
     - *Unit and property tests, lint, types*;
     - *PostgreSQL integration and migration tests*;
     - *Dependency and secret scan*;
     - *Terraform checks and IaC scan*;
     - *Image build, policy, scan, and container smoke*.
   - Block force pushes.
4. **Enable private vulnerability reporting** in **Settings → Code security**.
   [SECURITY.md](../../SECURITY.md) points reporters there.
5. **Deploy.** Push to `master`, or re-run the latest `CI` workflow. The
   *Deploy to dev* job then:
   - pushes the CI-tested image, tagged with its commit SHA, and attests its
     provenance;
   - runs migrations;
   - creates a new API revision with no traffic, and smoke-tests it at its
     own URL;
   - shifts 100 % of traffic to it.

   The job summary lists the revision and URL.

## Releasing a new version

- **dev:** merge to `master`. CI tests, deploys, and smoke-tests the commit.
- **production:**
  1. Tag a commit on `master` that has already passed through `dev`:
     `git tag -a v1.2.0 -m v1.2.0 <sha> && git push origin v1.2.0`.
  2. The release workflow reruns CI, then publishes a GitHub release with
     the image archive, the SBOM, and their provenance attestations.
  3. It waits for approval on the `production` environment, then deploys.
- **Rollback:** see the [rollback runbook](rollback-runbook.md).

Migrations must stay backward compatible with the previous release
(expand, then contract), because the previous revision stays ready for
rollback and old revisions keep serving until the switch.

### Infrastructure changes after the first deployment

`make tf-plan` and `make tf-apply` never change images or traffic. A plan
that changes the API's container template (for example an environment
variable or a probe) creates a new revision with **no traffic**, still on the
running image. To promote it, run a deployment: re-run the latest `CI`
workflow on `master`, or use `make azure-deploy` below. The relay and worker
use single-revision mode, so their template changes take effect straight
away.

### Break-glass release from a workstation

If GitHub Actions is unavailable:

1. Push the image as in
   [Build and push the image locally](#build-and-push-the-image-locally).
2. Run the same script CI uses:

   ```bash
   make azure-deploy IMAGE_TAG=$(git rev-parse HEAD)
   ```

The script performs every step of the CI deployment, using your Azure CLI
sign-in. Record why it was needed. An image deployed this way has no
provenance attestation.

## Verify

1. **API over HTTPS.**

   ```bash
   API=$(terraform -chdir=infra/azure output -raw api_url)
   curl -fsS "$API/health/ready"
   curl -sI "${API/https:/http:}/health/live" | head -1   # redirect to HTTPS
   ```

2. **Managed identity, not secrets.** Check that the apps have no registry
   password and that the database URL is a Key Vault reference:

   ```bash
   RG=$(terraform -chdir=infra/azure output -raw resource_group_name)
   az containerapp show -g "$RG" -n ca-codebreakers-dev-api \
     --query "{registries: properties.configuration.registries, secrets: properties.configuration.secrets}"
   ```

   `registries[0].identity` is the user-assigned identity, and the secret has
   a `keyVaultUrl` with no value.
3. **Telemetry.** After the smoke test, open Application Insights
   `appi-codebreakers-dev` and select **Transaction search**. Requests to
   `/v1/analyses` should appear within a few minutes. Container logs are in
   the `log-codebreakers-dev` workspace:

   ```kusto
   ContainerAppConsoleLogs_CL
   | where ContainerAppName_s startswith "ca-codebreakers-dev"
   | project TimeGenerated, ContainerAppName_s, Log_s
   | order by TimeGenerated desc
   ```

4. **Retention job.** Run it once by hand and check that it succeeds:

   ```bash
   scripts/run_azure_job.sh "$RG" caj-codebreakers-dev-retention
   ```

5. **Queue-driven scaling.** With the environment idle, the relay and worker
   should have no replicas. Submit a batch and watch them scale:

   ```bash
   watch -n 10 "az containerapp replica list -g $RG -n ca-codebreakers-dev-worker --query 'length(@)'"
   for i in $(seq 1 20); do
     curl -fsS -X POST "$API/v1/analyses" -H 'Content-Type: application/json' \
       -d '{"analyzer":"caesar-bruteforce","text":"Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj.","language":"english"}' > /dev/null
   done
   ```

   KEDA polls every 30 s. The relay starts first and moves the outbox rows to
   the queue. Then the worker scales towards `ceil(queued / 5)`, capped at 3,
   and returns to 0 about 5 minutes after the queue empties.

   The API allows 10 submissions per client IP per minute on each replica, so
   only the first 10 or so return `202`. The rest get `429` with
   `Retry-After`, which is expected.

6. **Alerts and dashboard.** In **Monitor → Alerts → Alert rules**, the
   resource group should have the API 5xx, restart, PostgreSQL,
   dead-letter, and queue-age rules. All of them use the action group
   `ag-codebreakers-dev`. Open the Application Insights workbook
   *Codebreakers service (dev)*. Then fire each alert once, following the
   **Test** steps in the [alerts runbook](alerts.md), and check that the
   email arrives.
7. **Listing disabled.** `curl -s -o /dev/null -w '%{http_code}' "$API/v1/analyses"`
   prints `404`. Job listing is off in Azure
   (`CODEBREAKERS_ANALYSIS_LISTING_ENABLED=false`); fetching by ID still works.

## Operating

- **Logs and revisions:**
  `az containerapp logs show -g "$RG" -n ca-codebreakers-dev-worker --follow`
  and `az containerapp revision list -g "$RG" -n ca-codebreakers-dev-api -o table`.
- **Dead letters:** PostgreSQL accepts only Azure traffic, so run the
  [dead-letter runbook](dead-letter-runbook.md) commands inside the API app,
  which has the same image and settings as the worker:

  ```bash
  curl -fsS "$API/health/live" > /dev/null   # wake a replica if scaled to zero
  az containerapp exec -g "$RG" -n ca-codebreakers-dev-api \
    --command "codebreakers deadletter list"
  ```

- **Pausing to save cost:** stopping the server saves about $12 a month. The
  API then reports not ready, and Azure restarts the server after 7 days.

  ```bash
  az postgres flexible-server stop -g "$RG" -n $(terraform -chdir=infra/azure output -raw postgres_server_name)
  az postgres flexible-server start -g "$RG" -n …
  ```

- **Budget alerts** go to `budget_contact_emails` at 80 % and 100 % of actual
  spend and 100 % of forecast. Spend is in **Cost Management → Cost analysis**,
  scoped to the resource group.
- **Operational alerts** go to the same addresses. Each alert links to its
  section in the [alerts runbook](alerts.md).
- **Other runbooks:**
  - [rollback](rollback-runbook.md);
  - [database restore](database-restore-runbook.md);
  - [credential compromise](credential-compromise-runbook.md);
  - [data deletion requests](../../SECURITY.md#data-deletion).

## Teardown and recreate

```bash
terraform -chdir=infra/azure destroy -var-file=environments/dev.tfvars \
  -var image_tag=$(git rev-parse HEAD)
```

- `dev` sets `protect_stateful_resources = false`, so the destroy deletes the
  database and its backups and purges the Key Vault. **This is irreversible.**
- To recreate, repeat [First deployment](#3-first-deployment). The random
  name suffix is part of state, so after a destroy a recreated environment
  gets new names and the old soft-deleted names cannot collide.
- The bootstrap state account is not touched.
- `prod` refuses to destroy while its delete lock and Key Vault purge
  protection exist. Remove them only after a deliberate decision and a backup
  plan.

## Troubleshooting

If `make azure-smoke` reports that the API never became ready, inspect the
API's system and console logs before increasing the timeout:

```bash
az containerapp logs show -g rg-codebreakers-dev -n ca-codebreakers-dev-api \
  --type system --tail 30 --follow false
az containerapp logs show -g rg-codebreakers-dev -n ca-codebreakers-dev-api \
  --type console --tail 60 --follow false
```

An `ImportError` mentioning `LogData` indicates an incompatible Azure Monitor
exporter/OpenTelemetry SDK in the image, not a database or ingress problem.
Use the corrected runtime lock file and commit the fix. Let CI deploy it, or
follow the [break-glass release](#break-glass-release-from-a-workstation).
`make image-check` checks the real telemetry imports in the runtime image.

| Symptom | Cause and fix |
|---------|---------------|
| `403` on `terraform init` or plan | The state account allows Entra auth only. Check `az account show`, and that the bootstrap granted you *Storage Blob Data Contributor*. Role assignments can take a few minutes. |
| Key Vault secret creation fails with `403 Forbidden` | Deployer RBAC had not propagated within the 60 s wait. Re-run `make tf-plan && make tf-apply`. |
| A revision is stuck in *Activating*, with `UNAUTHORIZED` pulling the image | Confirm the planned image tag exists using [Build and push the image locally](#build-and-push-the-image-locally). If it exists, check the app identity's AcrPull role and allow propagation time before restarting the revision. |
| `az acr build` fails because ACR Tasks are unavailable (some subscription types) | Use [Build and push the image locally](#build-and-push-the-image-locally) instead of `make azure-image`. |
| Local registry login or push is denied | Check the selected subscription and your identity's registry push permissions (AcrPush for a standard RBAC registry). Container Apps' AcrPull role permits pulls only; do not enable registry admin credentials to work around this. |
| `/health/ready` returns `503` | The database is stopped or migrations have not run (`make azure-migrate`). If the Key Vault secret cannot be read, the revision does not start at all: check `ContainerAppSystemLogs_CL`. |
| Jobs stay `pending` | Check that the relay and worker scale (Verify, step 5). The scale rules use the same Key Vault-backed database URL, so a broken secret reference shows up in `ContainerAppSystemLogs_CL`. |
| `LocationIsOfferRestricted` or quota errors for PostgreSQL | Some subscriptions cannot create Flexible Server in every region. Set `location` to another region in `local.auto.tfvars` and apply again. |
| `az containerapp exec` fails with "no replicas" | Wake the API with a request first, then retry. |
| *Deploy to dev* succeeds with the notice "environment has no Azure variables yet" and deploys nothing | The `dev` environment has no `AZURE_CLIENT_ID` variable. Complete [Connect GitHub Actions](#4-connect-github-actions). |
| `azure/login` fails with `AADSTS70021` (no matching federated identity) | The job did not run in the expected environment, or the repository name differs from `github_repository`. Check the federated credential subject `repo:<owner>/<repo>:environment:<env>`. |
| A deployment fails at the smoke test | Traffic stays on the previous revision, and the script restores the previous worker, relay, and job images. Read the job log and the new revision's console logs, then fix forward. |
| `make tf-plan` shows image or traffic changes | It should not: those fields are ignored. Check that `container_apps.tf` still has its `ignore_changes` blocks. |
