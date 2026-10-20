#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
state_directory=@STATE_DIRECTORY@
#include docker_setup.sh
if [ "$(sudo docker inspect --format='{{.State.Running}}' "$container_name" 2>/dev/null || true)" = true ]; then
  echo "MTProxy container is already running; leaving it unchanged"
  exit 0
fi
sudo install -d -m 700 "$state_directory"
if ! sudo test -s "$state_directory/secret"; then
  head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' \
    | sudo sh -c 'umask 077; cat > "$0/secret"' "$state_directory"
  echo "Generated a new MTProxy secret; it is stored root-only on this host"
fi
sudo docker pull @IMAGE@ >/dev/null
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  sudo docker rm -f "$container_name" >/dev/null
fi
sudo docker run --detach \
  --name "$container_name" \
  --restart unless-stopped \
  --publish @PORT@:443/tcp \
  --volume "$state_directory":/data \
  @IMAGE@ >/dev/null
echo "MTProxy container started on TCP port @PORT@"
