#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "Squid forward container is not running" >&2
  exit 1
fi
headers=$(curl --silent --show-error --output /dev/null --dump-header - \
  --max-time 30 --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@)
if ! printf '%s' "$headers" | grep -qiE '^(server|via|x-squid-error):.*squid'; then
  echo "HTTP request through the forward did not reach EU Squid" >&2
  exit 1
fi
echo "ru-server localhost TCP/@LOCAL_PORT@ forwards to EU Squid and carries HTTP"
