#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
for key in ("ru_server_address", "crawler_source_address", "dns_address",
            "vm_service_address", "outbound_address"):
    if ipaddress.ip_address(c[key]).version != 4:
        raise ValueError(key + " must be IPv4")
port = c["ssh_port"]
if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
    raise ValueError("invalid ssh_port")
for key in ("ru_server_address", "crawler_source_address", "ssh_port",
            "dns_address", "vm_service_address", "outbound_address"):
    print(c[key])
')
ru_address=${values[0]}
crawler_address=${values[1]}
ssh_port=${values[2]}
vm_service_address=${values[4]}
outbound_address=${values[5]}
vpn_port=@VPN_PORT@
proxy_ports=(@PROXY_PORTS@)
table=fck_rkn_eu

namespace=fck-rkn-eu-probe-$$
host_link=fcke$$
peer_link=fckq$$
host_address=198.18.0.1
stranger_address=198.18.0.2
rp_filter_path="/proc/sys/net/ipv4/conf/$host_link/rp_filter"
all_rp_filter_path=/proc/sys/net/ipv4/conf/all/rp_filter
original_rp_filter=
original_all_rp_filter=
work=$(mktemp -d)

cleanup() {
  sudo ip route del "$ru_address/32" dev "$host_link" >/dev/null 2>&1 || true
  if [ -n "$original_rp_filter" ] && [ -e "$rp_filter_path" ]; then
    printf '%s' "$original_rp_filter" | sudo tee "$rp_filter_path" >/dev/null || true
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

fail() {
  echo "$1" >&2
  exit 1
}

if ! sudo nft list table inet "$table" >/dev/null 2>&1; then
  fail "EU firewall table is not active"
fi
rules=$(sudo nft list table inet "$table")
if printf '%s\n' "$rules" | grep -q 'hook output'; then
  fail "EU firewall must not filter outgoing traffic"
fi
if ! printf '%s\n' "$rules" | grep -q 'hook input .*policy drop'; then
  fail "EU firewall input chain is not default deny"
fi

# Print "open", "refused" or "timeout" for a TCP connection from a source address.
probe() {
  sudo ip netns exec "$namespace" python3 -c '
import errno, socket, sys
source, target, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
sock = socket.socket()
sock.settimeout(3)
sock.bind((source, 0))
try:
    sock.connect((target, port))
    print("open")
except socket.timeout:
    print("timeout")
except ConnectionRefusedError:
    print("refused")
except OSError as error:
    print("error " + errno.errorcode.get(error.errno, "?"))
' "$@"
}

counter() {
  sudo nft list chain inet "$table" "$1" | python3 -c '
import re, sys
for line in sys.stdin:
    if sys.argv[1] in line:
        match = re.search(r"counter packets (\d+)", line)
        print(match.group(1) if match else "")
        break
' "$2"
}

report() {
  echo "PASS: $1"
}

expect_reachable() {
  local source=$1 port=$2 label=$3 outcome
  outcome=$(probe "$source" "$host_address" "$port")
  case "$outcome" in
    open|refused) ;;
    *) fail "Expected $label to be accepted but the result was: $outcome" ;;
  esac
  report "$label is accepted"
}

expect_denied() {
  local source=$1 port=$2 label=$3 outcome
  outcome=$(probe "$source" "$host_address" "$port")
  [ "$outcome" = timeout ] ||
    fail "Expected $label to be denied but the result was: $outcome"
  report "$label is denied"
}

sudo ip netns add "$namespace"
sudo ip link add "$host_link" type veth peer name "$peer_link"
sudo ip link set "$peer_link" netns "$namespace"
sudo ip address add "$host_address/30" dev "$host_link"
sudo ip link set "$host_link" up
sudo ip netns exec "$namespace" ip link set lo up
sudo ip netns exec "$namespace" ip address add "$stranger_address/30" dev "$peer_link"
sudo ip netns exec "$namespace" ip address add "$crawler_address/32" dev "$peer_link"
sudo ip netns exec "$namespace" ip address add "$ru_address/32" dev "$peer_link"
sudo ip netns exec "$namespace" ip link set "$peer_link" up
sudo ip netns exec "$namespace" ip route add "$host_address/32" dev "$peer_link"

# Replies to the probe that impersonates ru-server must reach the probe namespace.
sudo ip route add "$ru_address/32" dev "$host_link"
if [ -e "$all_rp_filter_path" ]; then
  original_all_rp_filter=$(cat "$all_rp_filter_path")
  printf '0' | sudo tee "$all_rp_filter_path" >/dev/null
fi
if [ -e "$rp_filter_path" ]; then
  original_rp_filter=$(cat "$rp_filter_path")
  printf '0' | sudo tee "$rp_filter_path" >/dev/null
fi

crawler_before=$(counter input "fck-rkn eu input crawler-v4:")
denied_before=$(counter input "fck-rkn eu input denied:")
forward_before=$(counter forward "fck-rkn eu forward direct-service:")
marker_time=$(date '+%Y-%m-%d %H:%M:%S')

[ "$(probe "$stranger_address" "$host_address" "$ssh_port")" = open ] ||
  fail "SSH from an arbitrary address was not accepted"
report "SSH from an arbitrary address is accepted"
[ "$(probe "$ru_address" "$host_address" "$ssh_port")" = open ] ||
  fail "SSH from ru-server was not accepted"
report "SSH from ru-server is accepted"
expect_reachable "$ru_address" "$vpn_port" "VPN port from ru-server"
expect_denied "$stranger_address" "$vpn_port" "VPN port from another host"
for port in "${proxy_ports[@]}"; do
  expect_denied "$stranger_address" "$port" "direct proxy port $port from another host"
  expect_reachable "$ru_address" "$port" "direct proxy port $port from ru-server"
done
expect_denied "$stranger_address" 9 "an unlisted port from another host"
#expect_reachable "$ru_address" 9 "an unlisted port from ru-server"
expect_denied "$crawler_address" "$ssh_port" "SSH from an RKN crawler range"

crawler_after=$(counter input "fck-rkn eu input crawler-v4:")
denied_after=$(counter input "fck-rkn eu input denied:")
forward_after=$(counter forward "fck-rkn eu forward direct-service:")
[ "${crawler_after:-0}" -gt "${crawler_before:-0}" ] ||
  fail "The crawler denial rule did not count the probe"
[ "${denied_after:-0}" -gt "${denied_before:-0}" ] ||
  fail "The default denial rule did not count the probe"
[ "${forward_after:-0}" -gt "${forward_before:-0}" ] ||
  fail "The direct proxy port denial rule did not count the probe"
report "Denied crawler, direct proxy-port and other incoming traffic is counted"

logged=false
for _ in $(seq 1 10); do
  kernel_log=$( (sudo journalctl -k --since "$marker_time" --no-pager 2>/dev/null ||
    sudo dmesg 2>/dev/null) | grep 'fck-rkn eu' || true)
  if printf '%s' "$kernel_log" | grep -q 'fck-rkn eu input crawler-v4' &&
     printf '%s' "$kernel_log" | grep -q 'fck-rkn eu input denied' &&
     printf '%s' "$kernel_log" | grep -q 'fck-rkn eu forward direct-service'; then
    logged=true
    break
  fi
  sleep 1
done
[ "$logged" = true ] || fail "Denied traffic was not written to the kernel log"
report "Denied traffic is written to the kernel log"

# Outgoing traffic is unrestricted and the minimal VM services keep working.
python3 - "$outbound_address" 443 <<'PY' || fail "Outgoing connection was blocked"
import socket, sys
socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=8).close()
PY
report "Outgoing connections are allowed and their replies return"
python3 - "$vm_service_address" 80 <<'PY' || fail "The VM service address is unreachable"
import socket, sys
socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=8).close()
PY
report "The VM service address is reachable"
getent hosts example.com >/dev/null || fail "Name resolution failed"
report "Name resolution works"

echo "EU firewall accepts SSH and VPN as required, denies and logs other incoming traffic, and leaves outgoing traffic open"
