#!/usr/bin/env bash
set -euo pipefail
if [ "$(sudo docker inspect --format='{{.State.Running}}' @CONTAINER_NAME@ 2>/dev/null || true)" != true ]; then
  echo 'MTProxy container is not running' >&2
  exit 1
fi
if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@PORT@'; then
  echo 'MTProxy TCP listener is unavailable' >&2
  exit 1
fi
secret_file=@STATE_DIRECTORY@/secret
if ! sudo grep -qE '^[0-9a-f]{32}$' "$secret_file"; then
  echo 'MTProxy secret is missing or malformed' >&2
  exit 1
fi
if [ "$(sudo stat -c %a "$secret_file")" != 600 ]; then
  echo 'MTProxy secret file is not root-only' >&2
  exit 1
fi
echo "MTProxy is running, TCP port @PORT@ accepts connections, and the secret is stored root-only"
