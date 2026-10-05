#!/usr/bin/env bash
# Verify the application containers shut down gracefully on SIGTERM.
#
# Stops api, relay, and worker with `docker compose stop` (SIGTERM, then
# SIGKILL after each service's stop_grace_period) and asserts that every
# process exited 0 and logged its own clean shutdown rather than being killed.
# Restart afterwards with `docker compose up --wait`.
set -euo pipefail

marker_for() {
  case "$1" in
    api) echo "Application shutdown complete" ;;
    relay) echo "relay_stopped" ;;
    worker) echo "worker_stopped" ;;
  esac
}

services=(api relay worker)
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose stop "${services[@]}"

failed=0
for service in "${services[@]}"; do
  container="$(docker compose ps --all --quiet "$service")"
  exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$container")"
  marker="$(marker_for "$service")"
  if [[ "$exit_code" != "0" ]]; then
    echo "FAIL $service exited with $exit_code (137 means it was killed)" >&2
    failed=1
  elif ! docker compose logs --no-color --since "$since" "$service" | grep -F "$marker" >/dev/null; then
    echo "FAIL $service exited without logging '$marker'" >&2
    failed=1
  else
    echo "ok   $service stopped gracefully"
  fi
done

exit "$failed"
