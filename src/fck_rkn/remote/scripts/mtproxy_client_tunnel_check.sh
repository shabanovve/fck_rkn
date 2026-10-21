#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "MTProxy tunnel container is not running" >&2
  exit 1
fi
config=$(sudo cat @STATE_DIRECTORY@/config.json)
if ! printf '%s' "$config" | python3 -c '
import json, sys
c = json.load(sys.stdin)
ok = (
    c["tunnel_address"] == "127.0.0.1:@LOCAL_PORT@"
    and c["local_address"] == "127.0.0.1"
    and str(c["local_port"]) == "@LOCAL_PORT@"
)
sys.exit(0 if ok else 1)
'; then
  echo "MTProxy tunnel is not configured for the RU Outline path" >&2
  exit 1
fi
read -r server_host server_port < <(printf '%s' "$config" | python3 -c \
  'import json,sys; c=json.load(sys.stdin); print(c["server"], c["server_port"])')
if ! timeout 10 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$server_host" "$server_port"; then
  echo "RU Outline server is unreachable from test-client" >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "MTProxy tunnel local listener is unavailable" >&2
  exit 1
fi
if ! timeout 10 bash -c 'head -c 64 /dev/urandom > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "MTProxy traffic did not reach the RU forward" >&2
  exit 1
fi
echo "test-client localhost TCP/@LOCAL_PORT@ reaches the RU MTProxy forward through Outline"
