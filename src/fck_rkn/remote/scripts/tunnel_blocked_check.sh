#!/usr/bin/env bash
set -euo pipefail
# Run while the tunnel is down: traffic must not get through, and nothing may
# fall back to a direct connection.
container_name=@CONTAINER_NAME@
if [ "$(sudo docker inspect --format="{{.State.Status}}" "$container_name" 2>/dev/null || true)" = running ]; then
  echo "Forward container is running; the tunnel is not lost" >&2
  exit 1
fi
if headers=$(curl --silent --output /dev/null --dump-header - \
  --max-time 15 --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@ 2>/dev/null) \
  && printf '%s' "$headers" | grep -qiE '^(server|via|x-squid-error):.*squid'; then
  echo "Traffic still reaches EU Squid while the tunnel is down" >&2
  exit 1
fi
echo "Traffic through the forward is blocked while the tunnel is down"
