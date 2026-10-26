#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
expected_image=@IMAGE@
if [ "$(sudo docker inspect --format="{{.Config.Image}}" "$container_name" 2>/dev/null || true)" != "$expected_image" ] \
  || [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "Outline Server container is not running with the expected image" >&2
  exit 1
fi
api_url="$(sudo sed -n 's/^apiUrl://p' @STATE_DIRECTORY@/access.txt)"
if [ -z "$api_url" ]; then
  echo "Outline Server API configuration is unavailable" >&2
  exit 1
fi
# The configured address may be a NAT address that is unreachable from the host itself.
loopback_url="$(printf '%s' "$api_url" | sed -E 's#^(https?://)\[?[^/]+\]?:([0-9]+)/#\1127.0.0.1:\2/#')"
fetch_keys() {
  curl --insecure --fail --silent --max-time 5 "$api_url/access-keys" 2>/dev/null ||
    curl --insecure --fail --silent --max-time 10 "$loopback_url/access-keys" 2>/dev/null
}
if ! fetch_keys \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); keys=data.get("accessKeys"); sys.exit(0 if isinstance(keys,list) and keys else 1)'; then
  echo "Outline Server authorized management endpoint is unhealthy" >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@KEYS_PORT@'; then
  echo "Outline Server access-key TCP endpoint is unavailable" >&2
  exit 1
fi
echo "Outline Server container, authorized API, and access endpoint are healthy"
