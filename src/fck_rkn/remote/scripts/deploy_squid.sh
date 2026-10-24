#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
config_directory=@CONFIG_DIRECTORY@
#include docker_setup.sh
sudo install -d -m 755 "$config_directory"
sudo tee "$config_directory/squid.conf" >/dev/null <<'SQUID_CONF'
#include squid.conf
SQUID_CONF
config_hash=$(sudo sha256sum "$config_directory/squid.conf" | cut -d' ' -f1)
# The container is kept only while it runs with the current access policy.
if [ "$(sudo docker inspect --format='{{.State.Running}} {{index .Config.Labels "fck-rkn.squid-config"}}' "$container_name" 2>/dev/null || true)" = "true $config_hash" ]; then
  echo "Squid container is already running with the current configuration; leaving it unchanged"
  exit 0
fi
sudo docker pull @IMAGE@
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  sudo docker rm -f "$container_name"
fi
sudo docker run --detach \
  --name "$container_name" \
  --label "fck-rkn.squid-config=$config_hash" \
  --restart unless-stopped \
  --publish @PORT@:@PORT@/tcp \
  --volume "$config_directory/squid.conf":/etc/squid/squid.conf:ro \
  @IMAGE@
