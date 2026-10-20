#!/usr/bin/env bash
set -euo pipefail
container_name=@CONTAINER_NAME@
state_directory=@STATE_DIRECTORY@
#include docker_setup.sh
# The launch label changes whenever the start command changes, so an older
# container (for example one stuck in a crash loop) is replaced.
launch_version=2
if [ "$(sudo docker inspect --format='{{.State.Running}} {{index .Config.Labels "fck-rkn.mtproxy-launch"}}' "$container_name" 2>/dev/null || true)" = "true $launch_version" ]; then
  echo "MTProxy container is already running; leaving it unchanged"
  exit 0
fi
sudo install -d -m 700 "$state_directory"
if ! sudo test -s "$state_directory/secret"; then
  head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' \
    | sudo sh -c 'umask 077; cat > "$0/secret"' "$state_directory"
  echo "Generated a new MTProxy secret; it is stored root-only on this host"
fi
sudo docker pull @IMAGE@ >/dev/null
if sudo docker container inspect "$container_name" >/dev/null 2>&1; then
  sudo docker rm -f "$container_name" >/dev/null
fi
# The image entrypoint asks a public service for the external IP and prints the
# secret to its logs; this host has no external IP, so start the proxy directly
# with the private address as the NAT address and without printing the secret.
host_ip=$(hostname -I | awk '{print $1}')
sudo docker run --detach \
  --name "$container_name" \
  --label "fck-rkn.mtproxy-launch=$launch_version" \
  --restart unless-stopped \
  --publish @PORT@:443/tcp \
  --volume "$state_directory":/data \
  --env "EXTERNAL_IP=$host_ip" \
  --entrypoint bash \
  @IMAGE@ -c '
set -euo pipefail
curl -fsS https://core.telegram.org/getProxyConfig -o /etc/telegram/backend.conf
internal_ip=$(ip -4 route get 8.8.8.8 | grep -Po "src\s+\d+\.\d+\.\d+\.\d+" | awk "{print \$2}")
exec /usr/local/bin/mtproto-proxy -p 2398 -H 443 -M 2 -C 60000 \
  --aes-pwd /etc/telegram/hello-explorers-how-are-you-doing -u root \
  /etc/telegram/backend.conf --allow-skip-dh \
  --nat-info "$internal_ip:$EXTERNAL_IP" -S "$(cat /data/secret)"
' >/dev/null
echo "MTProxy container started on TCP port @PORT@"
