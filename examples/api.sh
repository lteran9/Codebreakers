#!/usr/bin/env bash
# Walk through the Codebreakers HTTP API with curl.
#
#   BASE_URL=http://127.0.0.1:8000 examples/api.sh
#
# Start a server first: `codebreakers serve` (in-process queue) or
# `docker compose up --build --wait` (PostgreSQL queue). Only curl is required.
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
REQUEST_ID="${REQUEST_ID:-example-$(date +%s)}"

step() { printf '\n# %s\n' "$*"; }

post() {
  local path="$1" body="$2"
  printf '$ curl -X POST %s -d %s\n' "$path" "'$body'"
  curl --silent --show-error -X POST "$BASE_URL$path" \
    -H 'Content-Type: application/json' \
    -H "X-Request-ID: $REQUEST_ID" \
    --data "$body"
  printf '\n'
}

step "Liveness and readiness"
curl --silent --show-error "$BASE_URL/health/live"; printf '\n'
curl --silent --show-error "$BASE_URL/health/ready"; printf '\n'

step "Encrypt and decrypt with Vigenère"
post /v1/ciphers/vigenere/encrypt '{"text": "Attack at dawn", "key": "LEMON"}'
post /v1/ciphers/vigenere/decrypt '{"text": "Lxfopv ef rnhr", "key": "LEMON"}'

step "Caesar with a custom alphabet"
post /v1/ciphers/caesar/encrypt '{"text": "hello", "key": "3", "alphabet": "abcdefghijklmnopqrstuvwxyz"}'

step "Submit a queued analysis (202 Accepted + Location header)"
body='{"analyzer": "caesar-bruteforce", "text": "Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj"}'
printf '$ curl -X POST /v1/analyses -d %s\n' "'$body'"
location="$(
  curl --silent --show-error --fail -X POST "$BASE_URL/v1/analyses" \
    -H 'Content-Type: application/json' \
    -H "X-Request-ID: $REQUEST_ID" \
    --data "$body" \
    --dump-header - --output /dev/null |
    tr -d '\r' | sed -n 's/^[Ll]ocation: //p'
)"
echo "Location: $location"

step "Poll until the job finishes"
job=""
for _ in $(seq 1 60); do
  job="$(curl --silent --show-error -H "X-Request-ID: $REQUEST_ID" "$BASE_URL$location")"
  case "$job" in
    *'"status":"pending"'* | *'"status":"running"'*) sleep 1 ;;
    *) break ;;
  esac
done
echo "$job"

step "Errors are RFC 9457 problem details with a stable code"
post /v1/ciphers/caesar/encrypt '{"text": "hello", "key": "not-a-number"}'
post /v1/ciphers/enigma/encrypt '{"text": "hello", "key": "3"}'

printf '\nEvery request above carried X-Request-ID: %s\n' "$REQUEST_ID"
