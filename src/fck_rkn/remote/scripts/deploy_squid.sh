#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
#include docker_setup.sh
if [ "$(sudo docker inspect --format='{{.State.Running}}' "$container_name" 2>/dev/null || true)" = true ]; then
  echo "Squid container is already running; leaving it unchanged"
  exit 0
fi
sudo docker pull @IMAGE@
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  sudo docker rm -f "$container_name"
fi
sudo docker run --detach \
  --name "$container_name" \
  --restart unless-stopped \
  --publish @PORT@:@PORT@/tcp \
  @IMAGE@
