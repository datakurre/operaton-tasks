#!/usr/bin/env bash

set -euo pipefail

VERBOSE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--verbose]" >&2
      exit 1
      ;;
  esac
done

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
APP_BASE_URL=${APP_BASE_URL:-http://localhost:8080}
ENGINE_REST_URL=${ENGINE_REST_URL:-"$APP_BASE_URL/engine-rest/engine"}
COCKPIT_URL=${COCKPIT_URL:-"$APP_BASE_URL/operaton/app/cockpit/default/"}

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command curl

curl_request() {
  local args=(-sS)
  if [[ "$VERBOSE" == true ]]; then
    args+=(-v)
  fi

  curl "${args[@]}" "$@"
}

curl_request_strict() {
  local args=(-fsS)
  if [[ "$VERBOSE" == true ]]; then
    args+=(-v)
  fi

  curl "${args[@]}" "$@"
}

print_response_body() {
  local body_file=$1
  if [[ "$VERBOSE" != true ]]; then
    return 0
  fi

  echo "Response body:" >&2
  if [[ -s "$body_file" ]]; then
    cat "$body_file" >&2
    printf '\n' >&2
  else
    echo "<empty>" >&2
  fi
}

curl_capture_body_strict() {
  local body_file
  body_file=$(mktemp)

  curl_request_strict -o "$body_file" "$@"
  print_response_body "$body_file"
  cat "$body_file"
  rm -f "$body_file"
}

curl_capture_status() {
  local show_body=${1:-true}
  local body_file
  local status
  shift
  body_file=$(mktemp)

  status=$(curl_request -o "$body_file" -w '%{http_code}' "$@")
  if [[ "$show_body" == true ]]; then
    print_response_body "$body_file"
  fi
  rm -f "$body_file"
  printf '%s' "$status"
}

print_status() {
  local label=$1
  local actual=$2
  local expected=$3
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS  $label -> $actual"
  else
    echo "FAIL  $label -> expected $expected, got $actual" >&2
    exit 1
  fi
}

expect_2xx() {
  local label=$1
  local actual=$2
  if [[ "$actual" =~ ^2[0-9][0-9]$ ]]; then
    echo "PASS  $label -> $actual"
  else
    echo "FAIL  $label -> expected 2xx, got $actual" >&2
    exit 1
  fi
}

request_access_token() {
  if [[ -z "${OAUTH2_TOKEN_URL:-}" || -z "${OAUTH2_CLIENT_ID:-}" || -z "${OAUTH2_CLIENT_SECRET:-}" ]]; then
    return 1
  fi

  local response
  response=$(curl_capture_body_strict \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "client_id=$OAUTH2_CLIENT_ID" \
    --data-urlencode "client_secret=$OAUTH2_CLIENT_SECRET" \
    "$OAUTH2_TOKEN_URL")

  printf '%s' "$response" | tr -d '\n' | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

echo "Checking unauthenticated /engine-rest access"
unauthenticated_status=$(curl_capture_status true "$ENGINE_REST_URL")
expected_unauthenticated=${EXPECTED_UNAUTHENTICATED_STATUS:-401}
print_status "/engine-rest without token" "$unauthenticated_status" "$expected_unauthenticated"

if access_token=$(request_access_token); then
  echo "Checking authenticated /engine-rest access"
  authenticated_status=$(curl_capture_status true \
    -H "Authorization: Bearer $access_token" \
    "$ENGINE_REST_URL")
  expect_2xx "/engine-rest with bearer token" "$authenticated_status"
else
  echo "Skipping authenticated check; set OAUTH2_TOKEN_URL, OAUTH2_CLIENT_ID, and OAUTH2_CLIENT_SECRET to enable it"
fi

echo "Checking cockpit remains available"
webapp_headers=$(mktemp)
trap 'rm -f "$webapp_headers"' EXIT
webapp_status=$(curl_capture_status false -D "$webapp_headers" "$COCKPIT_URL")
expect_2xx "/operaton/app/cockpit/default/" "$webapp_status"
if grep -iq '^Content-Security-Policy:' "$webapp_headers"; then
  echo "PASS  cockpit CSP header present"
else
  echo "FAIL  cockpit CSP header missing" >&2
  exit 1
fi