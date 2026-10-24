#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
keys = ("eu_server_address", "eu_outline_address", "other_host_address",
        "dhcp_address", "vm_service_address", "ntp_address")
for key in keys:
    if ipaddress.ip_address(c[key]).version != 4:
        raise ValueError(key + " must be IPv4")
for key in ("eu_outline_port", "ssh_port"):
    port = c[key]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid " + key)
for key in keys + ("eu_outline_port", "ssh_port"):
    print(c[key])
for key in ("service_sets", "service_hosts"):
    items = c[key]
    if not items or not all(isinstance(i, str) and i.isascii() and
            all(ch.isalnum() or ch in "._-" for ch in i) for i in items):
        raise ValueError("invalid " + key)
    print(" ".join(items))
')
eu_server=${values[0]}
outline_server=${values[1]}
outside=${values[2]}
dhcp_address=${values[3]}
vm_service_address=${values[4]}
ntp_address=${values[5]}
outline_port=${values[6]}
ssh_port=${values[7]}
read -r -a service_sets <<<"${values[8]}"
read -r -a service_hosts <<<"${values[9]}"
vpn_port=@VPN_PORT@
proxy_ports=(@PROXY_PORTS@)
table=fck_rkn_ru

namespace=fck-rkn-ru-probe-$$
host_link=fckr$$
peer_link=fcks$$
host_address=198.18.0.1
stranger_address=198.18.0.2
probe_comment="fck-rkn-ru-probe-$$"
rp_filter_path="/proc/sys/net/ipv4/conf/$host_link/rp_filter"
all_rp_filter_path=/proc/sys/net/ipv4/conf/all/rp_filter
original_rp_filter=
original_all_rp_filter=

remove_probe_rules() {
  local chain handle
  for chain in input output forward; do
    for handle in $(sudo nft -a list chain inet "$table" "$chain" 2>/dev/null |
      sed -nE "/$probe_comment/ s/.*# handle ([0-9]+).*/\1/p"); do
      sudo nft delete rule inet "$table" "$chain" handle "$handle" \
        >/dev/null 2>&1 || true
    done
  done
}

cleanup() {
  remove_probe_rules
  if [ -n "$original_rp_filter" ] && [ -e "$rp_filter_path" ]; then
    printf '%s' "$original_rp_filter" | sudo tee "$rp_filter_path" >/dev/null || true
  fi
  if [ -n "$original_all_rp_filter" ]; then
    printf '%s' "$original_all_rp_filter" |
      sudo tee "$all_rp_filter_path" >/dev/null || true
  fi
  sudo ip netns del "$namespace" >/dev/null 2>&1 || true
  sudo ip link del "$host_link" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "$1" >&2
  exit 1
}

tcp_probe_code='
import errno, socket, sys
source, target, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
sock = socket.socket()
sock.settimeout(3)
if source:
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
'

# Print "open", "refused" or "timeout" for a TCP connection from the probe namespace.
ns_probe() {
  sudo ip netns exec "$namespace" python3 -c "$tcp_probe_code" "$@"
}

# Print the same outcomes for a TCP connection made by ru-server itself.
host_probe() {
  python3 -c "$tcp_probe_code" "" "$@"
}

ns_udp() {
  sudo ip netns exec "$namespace" python3 -c '
import socket, sys
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((sys.argv[1], int(sys.argv[2])))
for _ in range(3):
    sock.sendto(b"x", (sys.argv[3], int(sys.argv[4])))
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

# Insert a counting rule right before the final denial rule of the input chain.
insert_before_input_denial() {
  local comment=$1 handle
  shift
  handle=$(sudo nft -a list chain inet "$table" input)
  handle=$(sed -nE '/fck-rkn ru input denied:/ s/.*# handle ([0-9]+).*/\1/p' \
    <<<"$handle" | head -1)
  [ -n "$handle" ] || fail "Could not locate the input denial rule"
  sudo nft insert rule inet "$table" input position "$handle" \
    "$@" counter comment "\"$probe_comment-$comment\""
}

report() {
  echo "PASS: $1"
}

expect_accepted() {
  local source=$1 port=$2 label=$3 outcome
  outcome=$(ns_probe "$source" "$host_address" "$port")
  case "$outcome" in
    open|refused) ;;
    *) fail "Expected $label to be accepted but the result was: $outcome" ;;
  esac
  report "$label is accepted"
}

expect_denied() {
  local source=$1 port=$2 label=$3 outcome
  outcome=$(ns_probe "$source" "$host_address" "$port")
  [ "$outcome" = timeout ] ||
    fail "Expected $label to be denied but the result was: $outcome"
  report "$label is denied"
}

increased() {
  [ "${2:-0}" -gt "${1:-0}" ]
}

# 1. The active policy is default deny everywhere and has no log-only leftovers.
if ! sudo nft list table inet "$table" >/dev/null 2>&1; then
  fail "RU firewall table is not active"
fi
rules=$(sudo nft list table inet "$table")
for chain in input output forward; do
  chain_rules=$(sudo nft list chain inet "$table" "$chain")
  if ! grep -q 'policy drop' <<<"$chain_rules"; then
    fail "RU firewall $chain chain is not default deny: $(grep -m1 hook <<<"$chain_rules")"
  fi
done
if grep -Eq 'policy accept|default-policy|test-client' <<<"$rules"; then
  fail "RU firewall still contains log-only or test-client rules"
fi
log_rules=$(grep 'log prefix' <<<"$rules" || true)
if grep -Eq '[[:space:]](drop|accept)$' <<<"$log_rules"; then
  fail "A RU firewall log rule carries a verdict"
fi
for prefix in \
  "fck-rkn ru input crawler-v4:" \
  "fck-rkn ru input crawler-v6:" \
  "fck-rkn ru input denied:" \
  "fck-rkn ru output direct-eu-service:" \
  "fck-rkn ru output denied:" \
  "fck-rkn ru forward denied:"; do
  grep -Fq "$prefix" <<<"$rules" ||
    fail "RU firewall is missing the rule with the log prefix: $prefix"
done
for set_name in rkn_ipv4 rkn_ipv6; do
  set_rules=$(sudo nft list set inet "$table" "$set_name")
  set_rules=$(tr '\n' ' ' <<<"$set_rules")
  grep -Eq 'elements[[:space:]]*=[[:space:]]*\{[[:space:]]*[^}[:space:]][^}]*\}' \
    <<<"$set_rules" ||
    fail "RU firewall crawler set $set_name is missing or empty"
done
grep -q 'udp sport 68 udp dport 67 accept' <<<"$rules" ||
  fail "RU firewall does not allow outgoing DHCP renewal"
grep -q 'icmp type' <<<"$rules" ||
  fail "RU firewall does not allow ICMP on the local network"

for set_name in "${service_sets[@]}"; do
  grep -Eq "daddr @$set_name tcp dport .*accept" <<<"$rules" ||
    fail "RU firewall has no accept rule for the address set $set_name"
done

report "The active policy is default deny with separate log rules"

# 2. Build the isolated source used for the incoming probes.
sudo ip netns add "$namespace"
sudo ip link add "$host_link" type veth peer name "$peer_link"
sudo ip link set "$peer_link" netns "$namespace"
sudo ip address add "$host_address/30" dev "$host_link"
sudo ip link set "$host_link" up
sudo ip netns exec "$namespace" ip link set lo up
sudo ip netns exec "$namespace" ip address add "$stranger_address/30" dev "$peer_link"
sudo ip netns exec "$namespace" ip address add "$dhcp_address/32" dev "$peer_link"
sudo ip netns exec "$namespace" ip link set "$peer_link" up
sudo ip netns exec "$namespace" ip route add "$host_address/32" dev "$peer_link"
sudo ip netns exec "$namespace" ip route add default via "$host_address" dev "$peer_link"
if [ -e "$all_rp_filter_path" ]; then
  original_all_rp_filter=$(cat "$all_rp_filter_path")
  printf '0' | sudo tee "$all_rp_filter_path" >/dev/null
fi
if [ -e "$rp_filter_path" ]; then
  original_rp_filter=$(cat "$rp_filter_path")
  printf '0' | sudo tee "$rp_filter_path" >/dev/null
fi

input_denied_before=$(counter input "fck-rkn ru input denied:")
forward_denied_before=$(counter forward "fck-rkn ru forward denied:")
output_denied_before=$(counter output "fck-rkn ru output denied:")
direct_eu_before=$(counter output "fck-rkn ru output direct-eu-service:")
marker_time=$(date '+%Y-%m-%d %H:%M:%S')

# 3. Incoming traffic: SSH and VPN are accepted; everything else is denied.
expect_accepted "$stranger_address" "$ssh_port" "SSH from an arbitrary address"
expect_accepted "$stranger_address" "$vpn_port" "VPN port from an arbitrary address"
for port in "${proxy_ports[@]}"; do
  expect_denied "$stranger_address" "$port" "direct proxy port $port from another host"
done
expect_denied "$stranger_address" 9 "an unlisted port from another host"

input_denied_after=$(counter input "fck-rkn ru input denied:")
increased "$input_denied_before" "$input_denied_after" ||
  fail "The incoming denial rule did not count the probes"
report "Denied incoming traffic is counted"

# DHCP replies must pass: no DHCP packet may reach the final denial rule, while a
# control packet from the same source proves the probe packets arrive there.
insert_before_input_denial dhcp ip saddr "$dhcp_address" udp sport 67 udp dport 68
insert_before_input_denial control ip saddr "$dhcp_address" udp sport 1234 udp dport 68
ns_udp "$dhcp_address" 67 "$host_address" 68
ns_udp "$dhcp_address" 1234 "$host_address" 68
sleep 1
dhcp_denied=$(counter input "$probe_comment-dhcp")
control_denied=$(counter input "$probe_comment-control")
[ "${dhcp_denied:-0}" -eq 0 ] ||
  fail "A DHCP server reply was denied by the RU firewall"
increased 0 "${control_denied:-0}" ||
  fail "The DHCP control packets did not reach the RU firewall"
report "DHCP server replies are not denied"

# 4. Traffic routed through ru-server is denied.
forward_checked=false
if [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ]; then
  forwarded=$(ns_probe "$stranger_address" "$outside" 80)
  [ "$forwarded" = timeout ] ||
    fail "A routed connection beyond ru-server was not denied: $forwarded"
  forward_denied_after=$(counter forward "fck-rkn ru forward denied:")
  increased "$forward_denied_before" "$forward_denied_after" ||
    fail "The forwarding denial rule did not count the probe"
  forward_checked=true
  report "Routed traffic beyond ru-server is denied"
fi

# 5. Outgoing traffic: only the EU VPN transport and minimal VM services work.
[ "$(host_probe "$outline_server" "$outline_port")" = open ] ||
  fail "The VPN transport to eu-server is unreachable"
report "The VPN transport to eu-server is allowed"
for port in "${proxy_ports[@]}"; do
  [ "$(host_probe "$eu_server" "$port")" = timeout ] ||
    fail "Direct connection to eu-server TCP/$port was not denied"
done
direct_eu_after=$(counter output "fck-rkn ru output direct-eu-service:")
increased "$direct_eu_before" "$direct_eu_after" ||
  fail "The direct eu-server proxy-port rule did not count the probes"
report "Direct eu-server proxy ports are denied and counted"
[ "$(host_probe "$outside" 80)" = timeout ] ||
  fail "A connection to an external host was not denied"
if ping -c 1 -W 3 "$outside" >/dev/null 2>&1; then
  fail "An external host answered ICMP echo"
fi
output_denied_after=$(counter output "fck-rkn ru output denied:")
increased "$output_denied_before" "$output_denied_after" ||
  fail "The outgoing denial rule did not count the probes"
report "External hosts, including ICMP echo, are denied and counted"

service_marker=$(date '+%Y-%m-%d %H:%M:%S')
service_ips=("$vm_service_address")
for port in 80 443; do
  [ "$(host_probe "$vm_service_address" "$port")" = open ] ||
    fail "The VM service address is unreachable on TCP/$port"
  report "The VM service address is reachable on TCP/$port"
done
for service_host in "${service_hosts[@]}"; do
  service_ip=$(getent ahostsv4 "$service_host" | awk 'NR==1 {print $1}')
  [ -n "$service_ip" ] || fail "Could not resolve the service host $service_host"
  service_ips+=("$service_ip")
  [ "$(host_probe "$service_ip" 443)" = open ] ||
    fail "The service host $service_host ($service_ip) is unreachable on TCP/443"
  report "The service host $service_host is reachable on TCP/443"
done
sleep 2
service_log=$( (sudo journalctl -k --since "$service_marker" --no-pager 2>/dev/null ||
  sudo dmesg 2>/dev/null) | grep 'fck-rkn ru output denied' || true)
for service_ip in "${service_ips[@]}"; do
  if grep -Fq "DST=$service_ip " <<<"$service_log"; then
    fail "A denial was logged for the allowed service address $service_ip"
  fi
done
report "No denial is logged for the allowed service addresses"
getent hosts example.com >/dev/null || fail "Name resolution failed"
report "Name resolution works"
python3 - "$ntp_address" <<'PY' || fail "The time source did not answer an NTP request"
import socket, sys
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(4)
sock.sendto(b"\x1b" + 47 * b"\0", (sys.argv[1], 123))
if len(sock.recv(512)) < 48:
    raise SystemExit(1)
PY
report "The time source answers NTP requests"

# 6. Denials are written to the kernel log with the host prefix.
expected_prefixes=("fck-rkn ru input denied" "fck-rkn ru output denied"
  "fck-rkn ru output direct-eu-service")
if [ "$forward_checked" = true ]; then
  expected_prefixes+=("fck-rkn ru forward denied")
fi
logged=false
for _ in $(seq 1 10); do
  kernel_log=$( (sudo journalctl -k --since "$marker_time" --no-pager 2>/dev/null ||
    sudo dmesg 2>/dev/null) | grep 'fck-rkn ru' || true)
  missing=false
  for prefix in "${expected_prefixes[@]}"; do
    grep -q "$prefix" <<<"$kernel_log" || missing=true
  done
  if [ "$missing" = false ]; then
    logged=true
    break
  fi
  sleep 1
done
[ "$logged" = true ] || fail "Denied traffic was not written to the kernel log"
report "Denied traffic is written to the kernel log"

if [ "$forward_checked" = true ]; then
  forward_note="denies routed traffic"
else
  forward_note="has IP forwarding disabled, so the forward chain was not exercised"
fi
echo "RU firewall is default deny, accepts SSH, VPN and DHCP replies, denies and logs other traffic, allows only the EU VPN transport, minimal VM services and the allowed service networks outward, and $forward_note"
