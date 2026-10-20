#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
expected_image=@IMAGE@
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  actual_image="$(sudo docker inspect --format="{{.Config.Image}}" "$container_name")"
  actual_state="$(sudo docker inspect --format="{{.State.Status}}" "$container_name")"
  if [ "$actual_image" = "$expected_image" ] && [ "$actual_state" = running ]; then
    echo "Outline Server container is already running; leaving it unchanged"
    exit 0
  fi
  if [ "$actual_image" = @BASE_IMAGE@ ]; then
    echo "Replacing the stock Outline Server container with the patched image"
    sudo docker rm -f "$container_name" watchtower >/dev/null 2>&1 || true
  else
    echo "Existing Outline Server container differs; refusing to replace it" >&2
    exit 1
  fi
fi
if sudo docker container inspect watchtower >/dev/null 2>&1; then
  echo "A watchtower container already exists; refusing to replace it" >&2
  exit 1
fi
if ! sudo docker image inspect "$expected_image" >/dev/null 2>&1; then
  echo "The Outline Server image is missing; load it before deploying" >&2
  exit 1
fi
private_address=@PRIVATE_ADDRESS@
echo "Installing official Outline Server stable image on the private VM address"
curl --fail --silent --show-error @INSTALLER_URL@ | sudo env \
  CONTAINER_NAME="$container_name" \
  SB_IMAGE="$expected_image" \
  SHADOWBOX_DIR=@STATE_DIRECTORY@ \
  bash -s -- \
  --hostname "$private_address" \
  --api-port @API_PORT@ \
  --keys-port @KEYS_PORT@ \
  >/dev/null 2>&1 || {
  echo 'Official Outline Server installer failed; sensitive installer output was suppressed' >&2
  exit 1
}
echo "Outline Server deployment completed on the private VM address"
