#!/usr/bin/env bash
set -euo pipefail
# Fail-closed recovery: only restarts the tunnel containers and re-probes
# through the forward. It never changes routes or firewall rules and never
# issues a direct (non-tunnel) request.
attempts=@ATTEMPTS@
delay=@DELAY_SECONDS@
tunnel_up() {
  local headers
  headers=$(curl --silent --output /dev/null --dump-header - --max-time 20 \
    --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@ 2>/dev/null) || return 1
  printf '%s' "$headers" | grep -qiE '^(server|via|x-squid-error):.*squid'
}
for attempt in $(seq 1 "$attempts"); do
  if tunnel_up; then
    echo "Tunnel is up (attempt $attempt)"
    exit 0
  fi
  echo "Tunnel is down (attempt $attempt of $attempts); restarting tunnel containers"
  for container_name in @CLIENT_CONTAINER_NAME@ @FORWARD_CONTAINER_NAME@; do
    if ! sudo docker container inspect "$container_name" >/dev/null 2>&1; then
      echo "Container $container_name does not exist; cannot recover" >&2
      exit 1
    fi
    sudo docker restart "$container_name" >/dev/null
  done
  sleep "$delay"
done
if tunnel_up; then
  echo "Tunnel is up after recovery"
  exit 0
fi
echo "Tunnel did not recover after $attempts attempts; client traffic stays blocked" >&2
exit 1
