#!/usr/bin/env bash
set -euo pipefail
# Simulates tunnel loss by stopping only the forward container.
container_name=@CONTAINER_NAME@
if ! sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "Forward container does not exist; nothing to stop" >&2
  exit 1
fi
sudo docker stop "$container_name" >/dev/null
echo "Tunnel stopped: forward container is down"
