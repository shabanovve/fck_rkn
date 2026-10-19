#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
expected_image=@IMAGE@
patched=@PATCHED@
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  actual_image="$(sudo docker inspect --format="{{.Config.Image}}" "$container_name")"
  actual_state="$(sudo docker inspect --format="{{.State.Status}}" "$container_name")"
  if [ "$actual_image" = "$expected_image" ] && [ "$actual_state" = running ]; then
    echo "Outline Server container is already running; leaving it unchanged"
    exit 0
  fi
  if [ "$actual_image" = @BASE_IMAGE@ ] || [ "$actual_image" = @PATCHED_IMAGE@ ]; then
    echo "Replacing the Outline Server container with the requested image"
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
#include docker_setup.sh
if [ "$patched" = true ]; then
  echo "Building the Outline Server image without the public-IP target check"
  sudo docker build -t "$expected_image" -f - "$(mktemp -d)" <<'FCK_RKN_DOCKERFILE'
#include outline_shadowbox.Dockerfile
FCK_RKN_DOCKERFILE
fi
private_ip="$(curl --fail --silent --show-error -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip')"
echo "Installing official Outline Server stable image on the private VM address"
curl --fail --silent --show-error @INSTALLER_URL@ | sudo env \
  CONTAINER_NAME="$container_name" \
  SB_IMAGE="$expected_image" \
  SHADOWBOX_DIR=@STATE_DIRECTORY@ \
  bash -s -- \
  --hostname "$private_ip" \
  --api-port @API_PORT@ \
  --keys-port @KEYS_PORT@ \
  >/dev/null 2>&1 || {
  echo 'Official Outline Server installer failed; sensitive installer output was suppressed' >&2
  exit 1
}
echo "Outline Server deployment completed on the private VM address"
