#!/usr/bin/env bash
set -euo pipefail

eu_server_address="@EU_SERVER_ADDRESS@"

expect_blocked() {
  local host=$1 port=$2 label=$3
  if timeout 5 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$host" "$port" 2>/dev/null; then
    echo "Forbidden direct test-client path is reachable: $label" >&2
    return 1
  fi
}

for port in 3128 8443; do
  expect_blocked @RU_SERVER_ADDRESS@ "$port" "RU TCP/$port"
  if [[ -n "$eu_server_address" ]]; then
    expect_blocked "$eu_server_address" "$port" "EU TCP/$port"
  fi
done

if [[ -n "$eu_server_address" ]]; then
  echo "test-client cannot directly reach RU or EU web-proxy/MTProxy services"
else
  echo "test-client cannot directly reach RU web-proxy/MTProxy services"
fi
