#!/usr/bin/env bash
set -euo pipefail
expected_image=@IMAGE@
#include docker_setup.sh
if sudo docker image inspect "$expected_image" >/dev/null 2>&1; then
  echo "The Outline Server image is already present; skipping the load"
  exit 0
fi
echo "Loading the prebuilt Outline Server image"
gunzip | sudo docker load >/dev/null
echo "The Outline Server image is loaded"
