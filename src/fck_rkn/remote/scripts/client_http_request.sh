#!/usr/bin/env bash
set -euo pipefail
if ! headers=$(curl --silent --show-error --output /dev/null --dump-header - \
  --max-time 30 --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@); then
  echo "HTTP request through the proxy path failed" >&2
  exit 1
fi
status=$(printf '%s' "$headers" | head -n 1 | tr -d '\r' | cut -d' ' -f2)
echo "HTTP status through the proxy path: $status"
case "$status" in
  2??|3??) ;;
  *) echo "Unexpected HTTP status through the proxy path" >&2; exit 1 ;;
esac
if ! printf '%s' "$headers" | grep -qiE '^(server|via|x-squid-error):.*squid'; then
  echo "HTTP response did not come from EU Squid" >&2
  exit 1
fi
echo "HTTP response came from EU Squid through the proxy path"
