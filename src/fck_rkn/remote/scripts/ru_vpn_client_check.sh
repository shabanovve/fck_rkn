#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
image=@IMAGE@
local_port=@LOCAL_PORT@
squid_port=@SQUID_PORT@
mtproxy_port=@MTPROXY_PORT@
table=fck_rkn_ru
container_name=fck-rkn-fw-vpn-client-$$
probe_comment="fck-rkn-vpn-probe-$$"
state_directory=

readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
for key in ("other_host_address", "eu_server_address"):
    if ipaddress.ip_address(c[key]).version != 4:
        raise ValueError(key + " must be IPv4")
port = c["ssh_port"]
if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
    raise ValueError("invalid ssh_port")
print(c["other_host_address"])
print(c["eu_server_address"])
print(port)
')
outside=${values[0]}
eu_server=${values[1]}
ssh_port=${values[2]}

remove_probe_rules() {
  local handle
  for handle in $(sudo nft -a list chain inet "$table" output 2>/dev/null |
    sed -nE "/$probe_comment/ s/.*# handle ([0-9]+).*/\1/p"); do
    sudo nft delete rule inet "$table" output handle "$handle" \
      >/dev/null 2>&1 || true
  done
}

cleanup() {
  remove_probe_rules
  sudo docker rm -f "$container_name" >/dev/null 2>&1 || true
  if [ -n "$state_directory" ]; then
    sudo rm -rf "$state_directory"
  fi
}
trap cleanup EXIT

fail() {
  echo "$1" >&2
  exit 1
}

counter() {
  sudo nft list chain inet "$table" output | python3 -c '
import re, sys
for line in sys.stdin:
    if sys.argv[1] in line:
        match = re.search(r"counter packets (\d+)", line)
        print(match.group(1) if match else "")
        break
' "$1"
}

sudo docker image inspect "$image" >/dev/null 2>&1 ||
  fail "The Outline client image is not available locally"
state_directory=$(sudo mktemp -d)
sudo chmod 700 "$state_directory"
printf '%s' "$config" | python3 -c '
import json, sys
client = json.load(sys.stdin)["client"]
client["local_address"] = "127.0.0.1"
client["local_port"] = int(sys.argv[1])
print(json.dumps(client))
' "$local_port" | sudo sh -c 'umask 077; cat > "$0/config.json"' "$state_directory"
sudo docker run --detach --name "$container_name" --network host --user root \
  --volume "$state_directory":/etc/shadowsocks:ro \
  --entrypoint ss-local "$image" -c /etc/shadowsocks/config.json >/dev/null ||
  fail "Could not start the temporary VPN client"
ready=false
for _ in $(seq 1 30); do
  if timeout 2 bash -c 'echo > /dev/tcp/127.0.0.1/"$0"' "$local_port" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.5
done
[ "$ready" = true ] || fail "The temporary VPN client did not start listening"

sudo nft insert rule inet "$table" output oifname "lo" tcp dport "$mtproxy_port" \
  ct state new counter comment "\"$probe_comment\""

# Run one request through the VPN client. Print the first bytes the target returns
# as hex, or "none" if the target did not answer in time.
vpn_request() {
  python3 - "$local_port" "$1" "$2" "$3" "$4" <<'PY'
import binascii, ipaddress, socket, struct, sys
proxy_port, host, port, payload, wait = sys.argv[1:6]
sock = socket.create_connection(("127.0.0.1", int(proxy_port)), timeout=5)
sock.sendall(b"\x05\x01\x00")
if sock.recv(2) != b"\x05\x00":
    print("socks-refused")
    raise SystemExit
sock.sendall(
    b"\x05\x01\x00\x01"
    + ipaddress.IPv4Address(host).packed
    + struct.pack("!H", int(port))
)
reply = sock.recv(10)
if len(reply) < 2 or reply[1] != 0:
    print("none")
    raise SystemExit
sock.sendall(binascii.unhexlify(payload))
sock.settimeout(float(wait))
try:
    data = sock.recv(64)
except OSError:
    data = b""
print(binascii.hexlify(data).decode() if data else "none")
PY
}

hex_of() {
  printf '%s' "$1" | python3 -c 'import binascii,sys; print(binascii.hexlify(sys.stdin.buffer.read()).decode())'
}

# A VPN client can reach ru-server itself.
banner=$(vpn_request 127.0.0.1 "$ssh_port" "" 8)
[ "$(printf '%s' "$banner" | cut -c1-8)" = 5353482d ] ||
  fail "A VPN client could not reach ru-server itself over SSH"

# It reaches the web proxy, which relays through eu-server.
squid_reply=$(vpn_request 127.0.0.1 "$squid_port" \
  "$(hex_of $'GET http://youtube.com/ HTTP/1.1\r\nHost: youtube.com\r\nConnection: close\r\n\r\n')" 30)
[ "$(printf '%s' "$squid_reply" | cut -c1-10)" = 485454502f ] ||
  fail "A VPN client could not use the web proxy"

# It reaches the MTProto proxy port; the counter shows the packets arrived there.
vpn_request 127.0.0.1 "$mtproxy_port" "$(python3 -c 'print("00" * 64)')" 3 >/dev/null
mtproxy_packets=$(counter "$probe_comment")
[ "${mtproxy_packets:-0}" -ge 1 ] ||
  fail "A VPN client could not reach the MTProto proxy port"

# It cannot reach an external host or the EU proxy ports directly.
output_before=$(counter "fck-rkn ru output denied:")
direct_eu_before=$(counter "fck-rkn ru output direct-eu-service:")
http_request=$(hex_of $'GET / HTTP/1.0\r\n\r\n')
[ "$(vpn_request "$outside" 80 "$http_request" 6)" = none ] ||
  fail "A VPN client reached an external host"
output_after=$(counter "fck-rkn ru output denied:")
[ "${output_after:-0}" -gt "${output_before:-0}" ] ||
  fail "The outgoing denial rule did not count the VPN client request"
[ "$(vpn_request "$eu_server" "$squid_port" "$http_request" 6)" = none ] ||
  fail "A VPN client reached the EU proxy port directly"
direct_eu_after=$(counter "fck-rkn ru output direct-eu-service:")
[ "${direct_eu_after:-0}" -gt "${direct_eu_before:-0}" ] ||
  fail "The direct EU proxy-port rule did not count the VPN client request"

echo "VPN clients reach ru-server and its web and MTProto proxies, but not external hosts or the EU proxy ports directly"
