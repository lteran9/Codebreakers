#!/usr/bin/env bash
# Deploy one immutable image to an Azure environment with staged API traffic.
#
#   scripts/deploy_revision.sh <image-reference>
#
# 1. Pin API traffic to the revision serving it now.
# 2. Point the migration job at the new image and run it (migrations must be
#    backward compatible: the previous API keeps serving during the rollout).
# 3. Roll the retention job, worker, and relay to the new image.
# 4. Create a new API revision that receives no traffic, wait for it to
#    provision, and smoke-test it end to end on its own revision URL.
# 5. Only then send it 100% of traffic. The previous revision stays active so
#    scripts/rollback_revision.sh can return to it instantly.
#
# A failure before step 5 restores the previous worker, relay, and job images
# and deactivates the new revision; users never see the new API.
#
# Reads resource names from the environment (the Terraform
# `github_environment_variables` output): AZURE_RESOURCE_GROUP, AZURE_API_APP,
# AZURE_RELAY_APP, AZURE_WORKER_APP, AZURE_MIGRATE_JOB, AZURE_RETENTION_JOB.
# Optional: REVISION_SUFFIX, SMOKE_TIMEOUT (seconds, default 300).
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <registry>/codebreakers:<git-sha>" >&2
  exit 2
fi

image=$1
tag=${image##*:}
if [[ ! $tag =~ ^[0-9a-f]{40}$ ]]; then
  echo "Refusing to deploy $image: the tag must be a full Git commit SHA." >&2
  exit 2
fi

: "${AZURE_RESOURCE_GROUP:?}" "${AZURE_API_APP:?}" "${AZURE_RELAY_APP:?}"
: "${AZURE_WORKER_APP:?}" "${AZURE_MIGRATE_JOB:?}" "${AZURE_RETENTION_JOB:?}"
rg=$AZURE_RESOURCE_GROUP
script_dir=$(cd "$(dirname "$0")" && pwd)
# Revision suffixes: lowercase alphanumerics and '-', starting with a letter.
# Kept short so "<app>--<suffix>" stays well within revision name limits.
suffix=${REVISION_SUFFIX:-r${tag:0:7}-$(date -u +%y%m%d%H%M%S)}
new_revision="$AZURE_API_APP--$suffix"

az() { command az "$@" --only-show-errors; }

app_image() {
  az containerapp show -g "$rg" -n "$1" \
    --query 'properties.template.containers[0].image' -o tsv
}

job_image() {
  az containerapp job show -g "$rg" -n "$1" \
    --query 'properties.template.containers[0].image' -o tsv
}

serving_revision() {
  local name
  # shellcheck disable=SC2016 # JMESPath literal, not shell expansion
  name=$(az containerapp revision list -g "$rg" -n "$AZURE_API_APP" \
    --query 'sort_by([?properties.trafficWeight > `0`], &properties.trafficWeight)[-1].name' \
    -o tsv)
  if [[ -z $name ]]; then
    name=$(az containerapp show -g "$rg" -n "$AZURE_API_APP" \
      --query properties.latestReadyRevisionName -o tsv)
  fi
  echo "$name"
}

previous_worker=$(app_image "$AZURE_WORKER_APP")
previous_relay=$(app_image "$AZURE_RELAY_APP")
previous_migrate=$(job_image "$AZURE_MIGRATE_JOB")
previous_retention=$(job_image "$AZURE_RETENTION_JOB")
previous_revision=$(serving_revision)
api_created=0
traffic_shifted=0

restore() {
  local status=$?
  ((traffic_shifted)) && return
  echo "::error::Deployment failed; restoring previous images. API traffic stays on $previous_revision." >&2
  trap - ERR
  az containerapp update -g "$rg" -n "$AZURE_WORKER_APP" --image "$previous_worker" -o none || true
  az containerapp update -g "$rg" -n "$AZURE_RELAY_APP" --image "$previous_relay" -o none || true
  az containerapp job update -g "$rg" -n "$AZURE_MIGRATE_JOB" --image "$previous_migrate" -o none || true
  az containerapp job update -g "$rg" -n "$AZURE_RETENTION_JOB" --image "$previous_retention" -o none || true
  if ((api_created)); then
    az containerapp revision deactivate -g "$rg" -n "$AZURE_API_APP" \
      --revision "$new_revision" -o none || true
  fi
  exit "$status"
}
trap restore ERR INT TERM

echo "Pinning API traffic to $previous_revision"
az containerapp ingress traffic set -g "$rg" -n "$AZURE_API_APP" \
  --revision-weight "$previous_revision=100" -o none

echo "Running migrations with $image"
az containerapp job update -g "$rg" -n "$AZURE_MIGRATE_JOB" --image "$image" -o none
"$script_dir/run_azure_job.sh" "$rg" "$AZURE_MIGRATE_JOB"

echo "Rolling retention, worker, and relay to $image"
az containerapp job update -g "$rg" -n "$AZURE_RETENTION_JOB" --image "$image" -o none
az containerapp update -g "$rg" -n "$AZURE_WORKER_APP" --image "$image" -o none
az containerapp update -g "$rg" -n "$AZURE_RELAY_APP" --image "$image" -o none

echo "Creating API revision $new_revision with no traffic"
api_created=1
az containerapp update -g "$rg" -n "$AZURE_API_APP" --image "$image" \
  --revision-suffix "$suffix" -o none

deadline=$((SECONDS + 600))
while :; do
  state=$(az containerapp revision show -g "$rg" -n "$AZURE_API_APP" \
    --revision "$new_revision" --query properties.provisioningState -o tsv)
  case $state in
    Provisioned) break ;;
    Failed)
      echo "$new_revision failed to provision" >&2
      false
      ;;
  esac
  if ((SECONDS >= deadline)); then
    echo "Timed out waiting for $new_revision (state: ${state:-unknown})" >&2
    false
  fi
  sleep 5
done

revision_url="https://$(az containerapp revision show -g "$rg" -n "$AZURE_API_APP" \
  --revision "$new_revision" --query properties.fqdn -o tsv)"
echo "Smoke-testing $revision_url"
python3 "$script_dir/smoke_test.py" --base-url "$revision_url" \
  --timeout "${SMOKE_TIMEOUT:-300}"

echo "Shifting 100% of API traffic to $new_revision"
az containerapp ingress traffic set -g "$rg" -n "$AZURE_API_APP" \
  --revision-weight "$new_revision=100" -o none
traffic_shifted=1

# Keep only the new and previous revisions running: with a minimum replica
# count, every active revision costs compute.
for revision in $(az containerapp revision list -g "$rg" -n "$AZURE_API_APP" \
  --query '[?properties.active].name' -o tsv); do
  if [[ $revision != "$new_revision" && $revision != "$previous_revision" ]]; then
    echo "Deactivating $revision"
    az containerapp revision deactivate -g "$rg" -n "$AZURE_API_APP" \
      --revision "$revision" -o none
  fi
done

echo "Deployed $image as $new_revision (rollback target: $previous_revision)"
if [[ -n ${GITHUB_OUTPUT:-} ]]; then
  {
    echo "revision=$new_revision"
    echo "previous_revision=$previous_revision"
    echo "url=https://$(az containerapp show -g "$rg" -n "$AZURE_API_APP" \
      --query properties.configuration.ingress.fqdn -o tsv)"
  } >>"$GITHUB_OUTPUT"
fi
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  {
    echo "### Deployed \`$new_revision\`"
    echo "Image: \`$image\`"
    echo
    echo "Roll back with the **Rollback** workflow or:"
    echo "\`scripts/rollback_revision.sh $previous_revision\`"
  } >>"$GITHUB_STEP_SUMMARY"
fi
