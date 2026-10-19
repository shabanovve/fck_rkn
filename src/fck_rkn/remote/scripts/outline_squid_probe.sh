#!/usr/bin/env bash
set -euo pipefail
read -r method password host port
#include docker_setup.sh
container_name=@CONTAINER_NAME@
sudo docker rm -f "$container_name" >/dev/null 2>&1 || true
sudo docker pull @IMAGE@ >/dev/null
trap 'sudo docker rm -f "$container_name" >/dev/null 2>&1 || true' EXIT
sudo docker run --detach \
  --name "$container_name" --network host \
  --entrypoint ss-tunnel \
  @IMAGE@ \
  -s "$host" -p "$port" -k "$password" -m "$method" \
  -l @LOCAL_PORT@ -L "$host:@SQUID_PORT@" -v >/dev/null
sleep 3
diagnose() {
  echo "--- ss-tunnel container state and logs ---"
  sudo docker ps -a --filter name="$container_name" --format '{{.Status}}'
  sudo docker logs --tail 30 "$container_name" 2>&1 \
    | while IFS= read -r line; do echo "${line//"$password"/[redacted]}"; done
}
curl_err=$(mktemp)
if ! status=$(curl --verbose --silent --output /dev/null \
  --max-time 30 --write-out '%{http_code}' \
  --proxy http://127.0.0.1:@LOCAL_PORT@ @TARGET_URL@ \
  2>"$curl_err"); then
  echo "--- curl output ---"
  cat "$curl_err"
  diagnose
  exit 1
fi
echo "EU Squid answered through the Outline tunnel with HTTP $status"
