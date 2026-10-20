#!/usr/bin/env bash
set -euo pipefail
# Run while the tunnel is down: the MTProxy forward must not accept traffic.
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" = running ]; then
  echo "MTProxy forward container is running; the tunnel is not lost" >&2
  exit 1
fi
if timeout 5 bash -c 'exec 3<>"/dev/tcp/127.0.0.1/$0"' @LOCAL_PORT@ 2>/dev/null; then
  echo "The MTProxy forward still accepts connections while the tunnel is down" >&2
  exit 1
fi
echo "MTProxy forward is blocked while the tunnel is down"
