#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "MTProxy forward container is not running" >&2
  exit 1
fi
config=$(sudo cat @STATE_DIRECTORY@/config.json)
# The forward must go through the Outline server and end at the MTProxy port.
if ! python3 -c '
import json, sys
c = json.loads(sys.argv[1])
host = c["server"]
ok = (
    c["tunnel_address"] == "127.0.0.1:%s" % (sys.argv[2])
    and c["local_address"] in ("127.0.0.1", "0.0.0.0")
    and str(c["local_port"]) == sys.argv[2]
    and str(c["server_port"]) != sys.argv[2]
)
sys.exit(0 if ok else 1)
' "$config" @LOCAL_PORT@; then
  echo "MTProxy forward is not configured for the Outline path" >&2
  exit 1
fi
read -r server_host server_port < <(python3 -c "import json,sys; c=json.loads(sys.argv[1]); print(c['server'], c['server_port'])" "$config")
if ! timeout 10 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$server_host" "$server_port"; then
  echo "Outline server is unreachable from ru-server" >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "MTProxy forward local listener is unavailable" >&2
  exit 1
fi
# A handshake-sized write must be accepted by the forward.
if ! timeout 10 bash -c 'head -c 64 /dev/urandom > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "MTProxy forward did not accept traffic" >&2
  exit 1
fi
echo "ru-server localhost TCP/@LOCAL_PORT@ forwards to EU MTProxy through Outline"
