#!/usr/bin/env bash
# Start a Container Apps job execution and wait for a terminal status.
#
#   scripts/run_azure_job.sh <resource-group> <job-name> [timeout-seconds]
#
# Exits 0 only when the execution succeeds. Requires an authenticated `az`.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <resource-group> <job-name> [timeout-seconds]" >&2
  exit 2
fi

resource_group=$1
job=$2
timeout=${3:-600}

execution=$(az containerapp job start --resource-group "$resource_group" \
  --name "$job" --query name --output tsv)
echo "Started $job execution $execution"

deadline=$((SECONDS + timeout))
while :; do
  status=$(az containerapp job execution show --resource-group "$resource_group" \
    --name "$job" --job-execution-name "$execution" \
    --query properties.status --output tsv)
  case $status in
    Succeeded)
      echo "$execution succeeded"
      exit 0
      ;;
    Failed | Stopped | Degraded)
      echo "$execution finished with status $status; see the job logs in Log Analytics." >&2
      exit 1
      ;;
  esac
  if ((SECONDS >= deadline)); then
    echo "Timed out waiting for $execution (last status: ${status:-unknown})." >&2
    exit 1
  fi
  sleep 5
done
