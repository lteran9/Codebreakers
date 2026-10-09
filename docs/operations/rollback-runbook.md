# Runbook: Roll Back a Release

Return an environment to a previous healthy release. Every deployment creates
a new API revision. The previous revision stays active with no traffic, and
older ones are kept inactive (up to 10), so a rollback is a traffic switch
rather than a rebuild. The pipeline design is in
[ADR-0013](../architecture/ADR-0013-continuous-delivery.md).

## When to roll back

- An alert fires shortly after a deployment
  ([alerts runbook](alerts.md#first-steps-for-any-alert)).
- Smoke tests or users report errors that started with a release.
- A security fix needs time, and an earlier revision is not affected
  ([SECURITY.md](../../SECURITY.md)).

**Roll back first, investigate second.** It takes about a minute, and you can
deploy forward again afterwards.

## What a rollback does

`scripts/rollback_revision.sh`:

1. Finds the revision serving traffic and picks the target. The target is
   either the revision you name or the one created just before the serving
   revision.
2. Reactivates the target if it is inactive and waits until it is
   *Provisioned*. If it does not get there within 5 minutes, traffic stays
   where it was.
3. Sends 100 % of API traffic to the target.
4. Sets the worker, relay, migration job, and retention job to the target's
   image, so every component runs the same code.
5. Runs the smoke test against the public URL. Set `SKIP_SMOKE=1` to skip it.

It does **not** reverse database migrations. Releases ship only
backward-compatible (expand-then-contract) migrations, so the previous code
runs against the newer schema. If a release breaks that rule, see
[Schema problems](#schema-problems).

## Option A: GitHub Actions (preferred)

1. Open **Actions → Rollback → Run workflow**.
2. Choose the **environment** (`dev` or `production`). Leave **revision**
   blank to roll back one release, or enter a revision name from the list
   below.
3. `production` waits for a required reviewer to approve, just like a
   deployment.

The job signs in to Azure with the environment's OIDC identity. It uses the
`deploy-<env>` concurrency group, so it never runs at the same time as a
deployment. The run summary names the restored revision and image.

## Option B: from a workstation

You need Azure CLI access to the resource group and an initialised Terraform
backend (`make tf-init`):

```bash
make azure-rollback                                        # one release back
make azure-rollback ROLLBACK_TO=ca-codebreakers-dev-api--r1a2b3c4-260101120000
make azure-rollback TF_ENV=prod                            # after the prod backend is initialised
```

Revision names end in `r<first 7 characters of the commit SHA>-<UTC time as yymmddHHMMSS>`.
List them with:

```bash
RG=$(terraform -chdir=infra/azure output -raw resource_group_name)
APP=$(terraform -chdir=infra/azure output -json container_app_names | jq -r .api)
az containerapp revision list -g "$RG" -n "$APP" --all \
  --query "sort_by(@, &properties.createdTime)[].{name:name, active:properties.active, traffic:properties.trafficWeight, image:properties.template.containers[0].image}" \
  -o table
```

## Verify

1. The smoke test passed: the script prints `Rolled back to …`.
2. The traffic table shows the target at 100:
   `az containerapp ingress traffic show -g "$RG" -n "$APP" -o table`.
3. The alert that triggered the rollback resolves within its 15-minute
   window. The workbook shows errors and job outcomes returning to normal.
4. Every component runs the same image:

   ```bash
   for name in $(terraform -chdir=infra/azure output -json container_app_names | jq -r '.[]'); do
     az containerapp show -g "$RG" -n "$name" --query "[name, properties.template.containers[0].image]" -o tsv
   done
   ```

## Afterwards

- **Stop further deployments.** A push to `master` deploys `dev` again. Revert
  the bad commit, or merge a fix, so the next deployment goes forward.
- To return to the newer revision once it is fixed, run a normal deployment.
  A rollback that names the newer revision only works if it is still kept.
- Record what happened: the revision, the cause, and the alert. Put it in the
  incident issue or the release notes.

## Schema problems

If the newer migration is not compatible with the previous code (for example,
it dropped or renamed a column the old code reads):

1. Do not roll back blindly. The old revision will fail. Fix forward instead:
   ship a release that restores compatibility.
2. If the data itself is damaged, follow the
   [database restore runbook](database-restore-runbook.md).

Alembic downgrade is not part of rollback. Run it only by hand, after
reviewing the downgrade script, because downgrades can drop data.

## Rollback drill (dev)

Practise once per phase, or after changing the pipeline:

1. Deploy two commits to `dev` (two pushes to `master`, or
   `make azure-deploy IMAGE_TAG=<sha>` twice).
2. Run **Actions → Rollback** with `dev` and an empty revision.
3. Check the [Verify](#verify) steps. The traffic table shows the earlier
   revision at 100 %.
4. Deploy forward again by re-running the latest `CI` workflow on `master`.
