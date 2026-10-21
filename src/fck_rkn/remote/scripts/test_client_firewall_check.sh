#!/usr/bin/env bash
set -euo pipefail

expect_blocked() {
  local host=$1 port=$2 label=$3
  if timeout 5 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$host" "$port" 2>/dev/null; then
    echo "Forbidden direct test-client path is reachable: $label" >&2
    return 1
  fi
}

for port in 3128 8443; do
  expect_blocked @RU_SERVER_ADDRESS@ "$port" "RU TCP/$port"
  expect_blocked @EU_SERVER_ADDRESS@ "$port" "EU TCP/$port"
done
expect_blocked @RU_SERVER_ADDRESS@ 22 "RU SSH from a non-IAP client"

echo "test-client cannot directly reach RU or EU web-proxy/MTProxy services"
