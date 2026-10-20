#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" != running ]; then
  echo "Squid tunnel container is not running" >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@LOCAL_PORT@'; then
  echo "Squid tunnel local listener is unavailable" >&2
  exit 1
fi
echo "test-client localhost TCP/@LOCAL_PORT@ is listening"
