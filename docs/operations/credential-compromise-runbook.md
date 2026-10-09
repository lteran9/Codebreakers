# Runbook: Credential Compromise

Use this runbook when a credential that can reach Codebreakers might be
exposed. Examples: a secret pasted into a log, an issue, or a chat; a
suspicious workflow run; a lost laptop; or a vulnerability report. The assets
and trust boundaries are described in the
[threat model](../security/threat-model.md).

**Contain first:** revoke or rotate, then investigate. Rotations here cause
at most a few minutes of `503` in `dev`. Do not wait to be certain.

## Credential inventory

| Credential | Where it lives | Who uses it | If exposed |
| --- | --- | --- | --- |
| PostgreSQL admin password | Terraform state, Key Vault secret `database-url` | API, relay, worker, jobs, KEDA scale rules | [Rotate the database password](#database-password) |
| GitHub OIDC deploy identity `id-codebreakers-<env>-deploy` | No secret. A federated credential trusts GitHub environment `<env>` | `deploy.yml` and `rollback.yml` jobs | [Deploy identity](#deploy-identity) |
| Application identity `id-codebreakers-<env>` | No secret. A managed identity attached to apps and jobs | Image pulls, Key Vault read, telemetry export | [Container compromise](#container-or-application-identity) |
| GitHub accounts and tokens | github.com | Maintainers | [GitHub account or token](#github-account-or-token) |
| Operator Azure sign-in | Azure CLI token cache | Maintainers running Terraform and `az` | [Operator credentials](#operator-azure-credentials) |
| Terraform state | Bootstrap storage account (Entra auth only, no shared keys) | Terraform | Holds the database password: rotate it, and review who has *Storage Blob Data* roles on the account |
| Application Insights connection string | App environment variables | Telemetry export | Not a secret by itself: local auth is disabled, so ingestion needs the application identity |

GitHub holds no long-lived Azure secret. Its environment *variables* are
identifiers (client, tenant, and subscription IDs and resource names), not
credentials.

Set up variables used below:

```bash
TF="terraform -chdir=infra/azure"
RG=$($TF output -raw resource_group_name)
APPS=$($TF output -json container_app_names | jq -r '.[]')
```

## Database password

The password comes from `random_password.postgres`. Terraform writes it to
the server and to the Key Vault secret. Replacing the resource rotates both:

```bash
terraform -chdir=infra/azure plan -var-file=environments/dev.tfvars \
  -var image_tag=$(git rev-parse HEAD) \
  -replace=random_password.postgres -out=dev.tfplan
make tf-apply
```

The plan should replace the password and update only the server's
`administrator_password` and the `database-url` secret.

The apps read the secret through a versionless Key Vault reference, so they
need new replicas to pick up the new value:

```bash
for app in $APPS; do
  for rev in $(az containerapp revision list -g "$RG" -n "$app" --query "[?properties.active].name" -o tsv); do
    az containerapp revision restart -g "$RG" -n "$app" --revision "$rev"
  done
done
curl -fsS "$($TF output -raw api_url)/health/ready"
```

- **Readiness still reports `"database": "failing"`.** Container Apps can
  take up to 30 minutes to refresh a Key Vault reference. Wait, then restart
  again.
- **Jobs** (migrate, retention) read the secret on their next execution.

Then [check for misuse](#investigate). PostgreSQL logs are in the server's
**Monitoring → Logs** if diagnostic settings are enabled. Otherwise,
`pg_stat_activity` shows only the sessions open right now; look for
unfamiliar client addresses.

## Deploy identity

The identity can only be used by a GitHub Actions job running in the matching
GitHub environment of this repository. In practice, exposure means someone
could run workflows: through a compromised maintainer account, a malicious
merge to `master`, or a tampered action.

1. **Cut access.** Delete the federated credential. Deployments then fail at
   `azure/login`:

   ```bash
   az identity federated-credential delete -g "$RG" \
     --identity-name "id-codebreakers-dev-deploy" --name github-dev --yes
   ```

   In GitHub, also disable the `CI`, `Release`, and `Rollback` workflows
   (**Actions → workflow → ⋯ → Disable workflow**).
2. **Assume the database password is exposed too.** *Contributor* on the
   resource group can reset the server's admin password and run commands in
   containers. [Rotate it](#database-password).
3. **Check what ran.** Review the image on every app and job against known
   commits, and roll back if any is unknown ([rollback](rollback-runbook.md)).
   Check the registry for unexpected tags:

   ```bash
   for app in $APPS; do az containerapp show -g "$RG" -n "$app" --query "properties.template.containers[0].image" -o tsv; done
   az acr repository show-tags -n "$($TF output -raw container_registry_name)" --repository codebreakers --orderby time_desc --top 20 -o table
   ```

   Every deployed image has a provenance attestation. Verify one with:
   `gh attestation verify oci://<image> --repo lteran9/Codebreakers`.
4. **Restore.** After the cause is fixed, `make tf-plan` and `make tf-apply`
   recreate the federated credential. Re-enable the workflows.

## Container or application identity

If code execution inside a container is suspected (for example a dependency
compromise), the attacker had what the application identity has:

- reading `database-url`;
- pulling images;
- sending telemetry.

To respond:

1. [Roll back](rollback-runbook.md) to a known-good image, or deploy a fixed
   one.
2. [Rotate the database password](#database-password).
3. Review the [supply-chain controls](../security/threat-model.md): rebuild,
   check Trivy and Dependabot, and pin the fixed versions.

## GitHub account or token

1. Revoke the token (**Settings → Developer settings**), or sign out all
   sessions and reset the password and 2FA.
2. Review the account's security log, and the repository's recent pushes,
   workflow changes, environment settings, and branch protection rules.
3. If any workflow ran from unreviewed code, follow
   [Deploy identity](#deploy-identity).

## Operator Azure credentials

1. Revoke sessions for the user in **Microsoft Entra ID → Users → Revoke
   sessions**, and reset the password.
2. Review the **Activity log** for the subscription and the resource group
   (filter by *Event initiated by*), including role assignments and
   Key Vault access.
3. Rotate the database password: the operator is a *Key Vault Secrets
   Officer*.

## Investigate

- **Azure Activity log.** Filter the resource group by caller. The deploy
  identity's caller is its principal ID:
  `az identity show -g "$RG" -n id-codebreakers-dev-deploy --query principalId`.
- **Revisions.** List the revisions with their creation times and images
  ([rollback runbook](rollback-runbook.md#option-b-from-a-workstation)).
- **Telemetry** never holds submitted text, so access to Application
  Insights or Log Analytics alone does not expose user data.
- **User data.** The database holds at most 7 days of results, and source
  text only until a job finishes. If it was exposed, publish a notice and
  follow the [data deletion policy](../../SECURITY.md#data-deletion). The
  ciphers provide no confidentiality, but submissions may still be personal.

Record the timeline, what was rotated, and what follow-up is needed in a
private security advisory or incident issue.
