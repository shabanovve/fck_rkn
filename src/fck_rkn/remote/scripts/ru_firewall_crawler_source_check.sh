#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
ru = ipaddress.ip_address(c["ru_server_address"])
crawler = ipaddress.ip_address(c["crawler_source_address"])
if ru.version != 4 or crawler.version != 4:
    raise ValueError("crawler source test requires IPv4 addresses")
print(ru)
print(crawler)
')
ru_address=${values[0]}
crawler_address=${values[1]}
namespace=fck-rkn-crawler-probe-$$
host_link=fckc$$
peer_link=fckp$$
host_link_address=198.18.0.1/30
peer_link_address=198.18.0.2/30
probe_comment="fck-rkn-crawler-probe-$$"
listener_pid=
handle=
rp_filter_path="/proc/sys/net/ipv4/conf/$host_link/rp_filter"
original_rp_filter=
all_rp_filter_path=/proc/sys/net/ipv4/conf/all/rp_filter
original_all_rp_filter=
work=$(mktemp -d)

cleanup() {
  if [ -n "$handle" ]; then
    sudo nft delete rule inet fck_rkn_ru input handle "$handle" \
      >/dev/null 2>&1 || true
  fi
  if [ -n "$listener_pid" ]; then
    kill "$listener_pid" >/dev/null 2>&1 || true
    wait "$listener_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$original_rp_filter" ] && [ -e "$rp_filter_path" ]; then
    printf '%s' "$original_rp_filter" |
      sudo tee "$rp_filter_path" >/dev/null || true
  fi
  if [ -n "$original_all_rp_filter" ]; then
    printf '%s' "$original_all_rp_filter" |
      sudo tee "$all_rp_filter_path" >/dev/null || true
  fi
  sudo ip netns del "$namespace" >/dev/null 2>&1 || true
  sudo ip link del "$host_link" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

if ! sudo ip link show "$host_link" >/dev/null 2>&1 &&
   ! sudo ip netns list | grep -q "^$namespace"; then
  :
else
  echo "Crawler probe network name already exists" >&2
  exit 1
fi
if ! sudo nft list set inet fck_rkn_ru rkn_ipv4 |
  python3 -c '
import ipaddress, re, sys
address = ipaddress.ip_address(sys.argv[1])
content = sys.stdin.read()
elements = re.search(r"elements\s*=\s*\{([^}]*)\}", content, re.S)
if elements is None:
    sys.exit(1)
for value in elements.group(1).split(","):
    value = value.strip()
    if value and address in ipaddress.ip_network(value, strict=False):
        sys.exit(0)
sys.exit(1)
' "$crawler_address"; then
  echo "Crawler probe source is not present in the active IPv4 set" >&2
  exit 1
fi
crawler_counter() {
  sudo nft list chain inet fck_rkn_ru input | python3 -c '
import re, sys
for line in sys.stdin:
    if "fck-rkn ru input crawler-v4:" in line:
        match = re.search(r"counter packets (\d+)", line)
        print(match.group(1) if match else "")
        break
'
}
crawler_before=$(crawler_counter)
if [ -z "$crawler_before" ]; then
  echo "RU firewall crawler log rule is missing" >&2
  exit 1
fi

sudo ip netns add "$namespace"
sudo ip link add "$host_link" type veth peer name "$peer_link"
sudo ip link set "$peer_link" netns "$namespace"
sudo ip address add "$host_link_address" dev "$host_link"
sudo ip link set "$host_link" up
sudo ip netns exec "$namespace" ip link set lo up
sudo ip netns exec "$namespace" ip address add "$peer_link_address" dev "$peer_link"
sudo ip netns exec "$namespace" ip address add "$crawler_address/32" dev "$peer_link"
sudo ip netns exec "$namespace" ip link set "$peer_link" up
sudo ip netns exec "$namespace" ip route add "$ru_address/32" \
  via 198.18.0.1 dev "$peer_link" src "$crawler_address"

if [ -e "$all_rp_filter_path" ]; then
  original_all_rp_filter=$(cat "$all_rp_filter_path")
  printf '0' | sudo tee "$all_rp_filter_path" >/dev/null
fi
if [ -e "$rp_filter_path" ]; then
  original_rp_filter=$(cat "$rp_filter_path")
  printf '0' | sudo tee "$rp_filter_path" >/dev/null
fi

sudo nft insert rule inet fck_rkn_ru input ip saddr "$crawler_address" \
  counter comment "$probe_comment"
probe_rule=$(sudo nft -a list chain inet fck_rkn_ru input |
  grep "$probe_comment")
handle=$(printf '%s' "$probe_rule" | sed -nE 's/.*# handle ([0-9]+).*/\1/p')
if [ -z "$handle" ]; then
  echo "Could not locate the temporary crawler probe counter" >&2
  exit 1
fi

python3 - "$ru_address" "$work/port" <<'PY' &
import socket
import sys

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((sys.argv[1], 0))
server.listen(1)
server.settimeout(8)
with open(sys.argv[2], "w", encoding="ascii") as port_file:
    port_file.write(str(server.getsockname()[1]))
try:
    connection, _ = server.accept()
    connection.close()
except TimeoutError:
    pass
finally:
    server.close()
PY
listener_pid=$!
for _ in $(seq 1 20); do
  [ -s "$work/port" ] && break
  sleep 0.1
done
if [ ! -s "$work/port" ]; then
  echo "Could not start the RU crawler-probe listener" >&2
  exit 1
fi
test_port=$(cat "$work/port")
if ! kill -0 "$listener_pid" 2>/dev/null; then
  echo "RU crawler-probe listener exited before the test" >&2
  exit 1
fi
if sudo ip netns exec "$namespace" timeout 3 bash -c \
  'echo > /dev/tcp/"$0"/"$1"' "$ru_address" "$test_port" 2>/dev/null; then
  echo "A request from the crawler source reached the RU test listener" >&2
  exit 1
fi

probe_rule=$(sudo nft -a list chain inet fck_rkn_ru input |
  grep "$probe_comment")
packets=$(printf '%s' "$probe_rule" |
  sed -nE 's/.*counter packets ([0-9]+).*/\1/p')
if [ -z "$packets" ] || [ "$packets" -lt 1 ]; then
  echo "Crawler probe packet did not reach the RU firewall input chain" >&2
  exit 1
fi
crawler_after=$(crawler_counter)
if [ "${crawler_after:-0}" -le "$crawler_before" ]; then
  echo "The crawler log rule did not count the crawler-range request" >&2
  exit 1
fi
echo "RU firewall denied and logged a request from an active crawler-range source"
