# Runbook: Azure Environment

Deploy, verify, operate, and tear down the Azure environment described in
[ADR-0012](../architecture/ADR-0012-azure-dev-environment.md). Terraform
conventions are in [ADR-0011](../architecture/ADR-0011-terraform.md). The
commands use `dev`. For `prod`, add `TF_ENV=prod` to every `make` command and
use `environments/prod.tfvars`.

## Prerequisites

- An Azure subscription where you hold **Owner**, or **Contributor** plus
  **User Access Administrator**, because Terraform creates role assignments.
- Azure CLI 2.60+ with the extension: `az extension add --name containerapp --upgrade`.
- Terraform 1.16+ and Python 3.12+ (for the smoke test).
- Resource providers registered once per subscription:

  ```bash
  for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL \
            Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Insights \
            Microsoft.Storage Microsoft.Consumption; do
    az provider register --namespace "$ns"
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

1. Commit your work. The image tag is the Git SHA, and `make azure-image`
   refuses a dirty tree.
2. Create only the registry and its prerequisites:

   ```bash
   terraform -chdir=infra/azure apply -var-file=environments/dev.tfvars \
     -var image_tag=$(git rev-parse HEAD) -target=azurerm_container_registry.main
   ```

   Terraform warns that `-target` is for exceptional use. This is that case,
   and the next step applies the full configuration.
3. Build the image inside Azure (`linux/amd64`, no local Docker push):

   ```bash
   make azure-image
   ```

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

## Releasing a new version

```bash
git commit …            # or check out the commit to release
make azure-image
terraform -chdir=infra/azure plan \
  -var-file=environments/dev.tfvars \
  -var image_tag=$(git rev-parse HEAD) \
  -target='azurerm_container_app_job.job["migrate"]' \
  -out=dev-migrate.tfplan
terraform -chdir=infra/azure apply dev-migrate.tfplan
make azure-migrate
make tf-plan            # only the image tag on 3 apps and 2 jobs should change
make tf-apply
make azure-smoke
```

The targeted apply updates only the migration job to the new image, so its
migrations run before the full apply updates the long-running apps. Replace
`dev` with `prod` in the plan command when releasing to production. Migrations
must stay backwards compatible with the previous release, since old API
revisions keep serving until their replacements are ready (ADR-0010).

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

| Symptom | Cause and fix |
|---------|---------------|
| `403` on `terraform init` or plan | The state account allows Entra auth only. Check `az account show`, and that the bootstrap granted you *Storage Blob Data Contributor*. Role assignments can take a few minutes. |
| Key Vault secret creation fails with `403 Forbidden` | Deployer RBAC had not propagated within the 60 s wait. Re-run `make tf-plan && make tf-apply`. |
| A revision is stuck in *Activating*, with `UNAUTHORIZED` pulling the image | The image tag does not exist in the registry (run `make azure-image` for the planned SHA), or AcrPull has not propagated yet (wait, then `az containerapp revision restart`). |
| `az acr build` fails because ACR Tasks are unavailable (some subscription types) | Build and push locally: `az acr login -n <registry>`, then `docker buildx build --platform linux/amd64 -t <login_server>/codebreakers:$(git rev-parse HEAD) --push .` |
| `/health/ready` returns `503` | The database is stopped or migrations have not run (`make azure-migrate`). If the Key Vault secret cannot be read, the revision does not start at all: check `ContainerAppSystemLogs_CL`. |
| Jobs stay `pending` | Check that the relay and worker scale (Verify, step 5). The scale rules use the same Key Vault-backed database URL, so a broken secret reference shows up in `ContainerAppSystemLogs_CL`. |
| `LocationIsOfferRestricted` or quota errors for PostgreSQL | Some subscriptions cannot create Flexible Server in every region. Set `location` to another region in `local.auto.tfvars` and apply again. |
| `az containerapp exec` fails with "no replicas" | Wake the API with a request first, then retry. |
