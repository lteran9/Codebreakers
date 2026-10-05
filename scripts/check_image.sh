#!/usr/bin/env bash
# Assert runtime image policy: non-root user, no development tooling, and no
# credentials in the image configuration, environment, or build history.
# Layer contents are secret-scanned separately by `make scan`.
set -euo pipefail

image="${1:-codebreakers:local}"
failed=0

fail() {
  echo "FAIL $*" >&2
  failed=1
}

user="$(docker inspect --format '{{.Config.User}}' "$image")"
case "${user%%:*}" in
  "" | 0 | root) fail "image USER is '${user:-unset}'" ;;
  *) echo "ok   image USER is $user" ;;
esac

uid="$(docker run --rm --entrypoint id "$image" -u)"
[[ "$uid" != "0" ]] && echo "ok   container runs as uid $uid" || fail "container runs as root"

if docker run --rm --entrypoint python "$image" -c '
import importlib.util, sys
tools = ["pip", "pytest", "mypy", "ruff", "hypothesis", "testcontainers"]
sys.exit(any(importlib.util.find_spec(name) for name in tools))'; then
  echo "ok   no pip or development tools installed"
else
  fail "pip or development tools are installed"
fi

pattern='(password|passwd|secret|token|connection_?string|access_?key|database_url)='
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" | grep -Eiq "$pattern"; then
  fail "credential-like environment variable baked into the image"
else
  echo "ok   no credentials in image environment"
fi
if docker history --no-trunc --format '{{.CreatedBy}}' "$image" | grep -Eiq "$pattern"; then
  fail "credential-like build argument or command in image history"
else
  echo "ok   no credentials in build history or arguments"
fi

exit "$failed"
