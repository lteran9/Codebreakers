#!/usr/bin/env bash
# Return API traffic to a previous healthy revision and realign the worker,
# relay, and jobs with that revision's image.
#
#   scripts/rollback_revision.sh [revision-name]
#
# Without an argument, rolls back to the revision created immediately before
# the one serving traffic now. Inactive revisions are reactivated first.
# Database migrations are not reversed: deployments only ship backward-
# compatible (expand-then-contract) migrations, so the previous code runs
# against the newer schema. See docs/operations/rollback-runbook.md.
#
# Reads AZURE_RESOURCE_GROUP, AZURE_API_APP, AZURE_RELAY_APP,
# AZURE_WORKER_APP, AZURE_MIGRATE_JOB, AZURE_RETENTION_JOB from the
# environment. Set SKIP_SMOKE=1 to skip the post-rollback smoke test.
set -Eeuo pipefail

: "${AZURE_RESOURCE_GROUP:?}" "${AZURE_API_APP:?}" "${AZURE_RELAY_APP:?}"
: "${AZURE_WORKER_APP:?}" "${AZURE_MIGRATE_JOB:?}" "${AZURE_RETENTION_JOB:?}"
rg=$AZURE_RESOURCE_GROUP
app=$AZURE_API_APP
script_dir=$(cd "$(dirname "$0")" && pwd)

az() { command az "$@" --only-show-errors; }

# shellcheck disable=SC2016 # JMESPath literal, not shell expansion
current=$(az containerapp revision list -g "$rg" -n "$app" \
  --query 'sort_by([?properties.trafficWeight > `0`], &properties.trafficWeight)[-1].name' \
  -o tsv)

if [[ $# -ge 1 ]]; then
  target=$1
else
  # Revisions oldest first; the target is the one created just before current.
  target=""
  while IFS= read -r revision; do
    [[ $revision == "$current" ]] && break
    target=$revision
  done < <(az containerapp revision list -g "$rg" -n "$app" --all \
    --query 'sort_by(@, &properties.createdTime)[].name' -o tsv)
fi

if [[ -z $target || $target == "$current" ]]; then
  echo "No earlier revision to roll back to (serving: ${current:-none})." >&2
  exit 1
fi

if [[ $(az containerapp revision show -g "$rg" -n "$app" --revision "$target" \
  --query properties.active -o tsv) != "true" ]]; then
  echo "Activating $target"
  az containerapp revision activate -g "$rg" -n "$app" --revision "$target" -o none
fi

deadline=$((SECONDS + 300))
until [[ $(az containerapp revision show -g "$rg" -n "$app" --revision "$target" \
  --query properties.provisioningState -o tsv) == "Provisioned" ]]; do
  if ((SECONDS >= deadline)); then
    echo "$target did not become ready; traffic is unchanged on $current." >&2
    exit 1
  fi
  sleep 5
done

image=$(az containerapp revision show -g "$rg" -n "$app" --revision "$target" \
  --query 'properties.template.containers[0].image' -o tsv)

if [[ ${SKIP_SMOKE:-0} != "1" ]]; then
  url="https://$(az containerapp revision show -g "$rg" -n "$app" --revision "$target" \
    --query properties.fqdn -o tsv)"
  echo "Smoke-testing $url"
  python3 "$script_dir/smoke_test.py" --base-url "$url" --timeout 300
fi

echo "Shifting 100% of API traffic from ${current:-none} to $target"
az containerapp ingress traffic set -g "$rg" -n "$app" \
  --revision-weight "$target=100" -o none

echo "Realigning worker, relay, and jobs with $image"
az containerapp update -g "$rg" -n "$AZURE_WORKER_APP" --image "$image" -o none
az containerapp update -g "$rg" -n "$AZURE_RELAY_APP" --image "$image" -o none
az containerapp job update -g "$rg" -n "$AZURE_MIGRATE_JOB" --image "$image" -o none
az containerapp job update -g "$rg" -n "$AZURE_RETENTION_JOB" --image "$image" -o none

echo "Rolled back to $target ($image)"
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  echo "### Rolled back to \`$target\`"$'\n'"Image: \`$image\`" >>"$GITHUB_STEP_SUMMARY"
fi
