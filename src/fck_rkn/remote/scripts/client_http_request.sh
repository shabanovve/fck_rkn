#!/usr/bin/env bash
set -euo pipefail
if ! status=$(curl --silent --output /dev/null --max-time 30 \
  --write-out '%{http_code}' --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@); then
  echo "HTTP request through the proxy path failed" >&2
  exit 1
fi
echo "HTTP status through the proxy path: $status"
