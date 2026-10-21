#!/usr/bin/env bash
set -euo pipefail
expected_image=@IMAGE@
#include docker_setup.sh
if sudo docker image inspect "$expected_image" >/dev/null 2>&1; then
  echo "The Outline Server image is already present; skipping the load"
  exit 0
fi
echo "Loading the prebuilt Outline Server image: 0%"
python3 -c '
import sys

total = int(sys.argv[1])
processed = 0
reported = 0
while True:
    chunk = sys.stdin.buffer.read(64 * 1024)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    processed += len(chunk)
    percent = min(99, processed * 100 // total)
    if percent >= reported + 5:
        reported = percent // 5 * 5
        print(f"Loading the prebuilt Outline Server image: {reported}%", file=sys.stderr, flush=True)
' @ARCHIVE_SIZE@ | gunzip | sudo docker load >/dev/null
echo "Loading the prebuilt Outline Server image: 100%"
echo "The Outline Server image is loaded"
