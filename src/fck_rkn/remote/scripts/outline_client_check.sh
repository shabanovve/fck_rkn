#!/usr/bin/env bash
set -euo pipefail
expected=$(cat)
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.Config.Image}}" "$container_name" 2>/dev/null || true)" != @IMAGE@ ] \
  || [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "Outline client container is not running with the expected image" >&2
  exit 1
fi
actual=$(sudo cat @STATE_DIRECTORY@/config.json)
if [ "$actual" != "$expected" ]; then
  echo "Outline client is not configured with the test key" >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "Outline client local listener is unavailable" >&2
  exit 1
fi
server_host=$(python3 -c "import json,sys; c=json.loads(sys.argv[1]); print(c['server'], c['server_port'])" "$expected")
read -r server_host server_port <<<"$server_host"
if ! timeout 10 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$server_host" "$server_port"; then
  echo "EU Outline server is unreachable from the client" >&2
  exit 1
fi
if ! status=$(curl --silent --output /dev/null --max-time 30 \
  --write-out '%{http_code}' \
  --socks5-hostname 127.0.0.1:@LOCAL_PORT@ @TARGET_URL@); then
  echo "HTTP request through the Outline client failed" >&2
  exit 1
fi
case "$status" in
  2??|3??) ;;
  *) echo "Unexpected HTTP status through the Outline client: $status" >&2; exit 1 ;;
esac
echo "Outline client is running, uses the test key, and carries HTTP through the tunnel"
