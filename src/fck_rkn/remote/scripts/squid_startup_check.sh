#!/usr/bin/env bash
if [ "$(sudo docker inspect --format='{{.State.Running}}' @CONTAINER_NAME@)" != true ]; then
  echo 'Squid container is not running' >&2
  exit 1
fi
timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/@PORT@' \
  || { echo 'Squid TCP listener is unavailable' >&2; exit 1; }
