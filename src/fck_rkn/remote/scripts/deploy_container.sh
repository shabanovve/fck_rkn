#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
container_name=@CONTAINER_NAME@
expected_image=@IMAGE@
#include docker_setup.sh
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  actual_image="$(sudo docker inspect --format="{{.Config.Image}}" "$container_name")"
  actual_state="$(sudo docker inspect --format="{{.State.Status}}" "$container_name")"
  if [ "$actual_image" = "$expected_image" ] && [ "$actual_state" = running ]; then
    echo "@TITLE@ container is already running; leaving it unchanged"
    exit 0
  fi
  if [ "$actual_state" = running ]; then
    echo "Existing @TITLE@ container uses a different image; refusing to replace it" >&2
    exit 1
  fi
  echo "Existing @TITLE@ container is not running; recreating it"
  sudo docker rm -f "$container_name" >/dev/null
fi
sudo install -d -m 700 @STATE_DIRECTORY@
printf '%s' "$config" | sudo sh -c 'umask 077; cat > @STATE_DIRECTORY@/config.json'
sudo docker pull "$expected_image" >/dev/null
sudo docker run --detach \
  --name "$container_name" \
  --restart unless-stopped --network host --user root \
  --volume @STATE_DIRECTORY@:/etc/shadowsocks:ro \
  --entrypoint @ENTRYPOINT@ \
  "$expected_image" -c /etc/shadowsocks/config.json >/dev/null
echo "@TITLE@ container started"
