#!/usr/bin/env bash
set -euo pipefail
# Fail-closed recovery: only restarts the MTProxy forward container and waits
# for its local listener. It never changes routes or firewall rules.
container_name=@CONTAINER_NAME@
if ! sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "Container $container_name does not exist; cannot recover" >&2
  exit 1
fi
sudo docker restart "$container_name" >/dev/null
for attempt in $(seq 1 @ATTEMPTS@); do
  if timeout 5 bash -c 'exec 3<>"/dev/tcp/127.0.0.1/$0"' @LOCAL_PORT@ 2>/dev/null; then
    echo "MTProxy forward is up (attempt $attempt)"
    exit 0
  fi
  sleep @DELAY_SECONDS@
done
echo "MTProxy forward did not recover; traffic stays blocked" >&2
exit 1
