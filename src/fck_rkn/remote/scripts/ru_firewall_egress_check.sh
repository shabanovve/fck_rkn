#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
for key in ("eu_server_address", "eu_outline_address", "test_client_address", "other_host_address"):
    ipaddress.ip_address(c[key])
port = c["eu_outline_port"]
if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
    raise ValueError("invalid EU Outline port")
if c["test_client_address"] in (c["eu_server_address"], c["eu_outline_address"]):
    raise ValueError("test-client address must be distinct")
if c["other_host_address"] in (c["eu_server_address"], c["eu_outline_address"], c["test_client_address"]):
    raise ValueError("firewall test hosts must be distinct")
print(c["eu_server_address"])
print(c["eu_outline_address"])
print(c["test_client_address"])
print(c["other_host_address"])
print(port)
')
eu_server=${values[0]}
outline_server=${values[1]}
test_client=${values[2]}
other_host=${values[3]}
outline_port=${values[4]}

if ! sudo nft list chain inet fck_rkn_ru output | grep -q 'policy drop'; then
  echo "RU firewall output chain is missing its drop policy" >&2
  exit 1
fi
if ! sudo nft list table inet fck_rkn_ru >/dev/null; then
  echo "RU firewall table is not active" >&2
  exit 1
fi
for set_name in rkn_ipv4 rkn_ipv6; do
  if ! sudo nft list set inet fck_rkn_ru "$set_name" |
    grep -Eq 'elements[[:space:]]*=[[:space:]]*\{[[:space:]]*[^}]+[[:space:]]*\}'; then
    echo "RU firewall crawler set $set_name is missing or empty" >&2
    exit 1
  fi
done

expect_reachable() {
  local host=$1 port=$2 label=$3
  if ! timeout 5 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$host" "$port"; then
    echo "Allowed RU firewall path is unreachable: $label" >&2
    return 1
  fi
}

expect_blocked() {
  local host=$1 port=$2 label=$3
  if timeout 5 bash -c 'echo > /dev/tcp/"$0"/"$1"' "$host" "$port" 2>/dev/null; then
    echo "Forbidden RU firewall path is reachable: $label" >&2
    return 1
  fi
}

expect_reachable "$outline_server" "$outline_port" "RU-to-EU Outline transport"
for port in 3128 8443; do
  expect_blocked "$eu_server" "$port" "direct RU-to-EU service TCP/$port"
  expect_blocked "$test_client" "$port" "RU-to-test-client TCP/$port"
  expect_blocked "$other_host" "$port" "RU-to-other-host TCP/$port"
done
expect_blocked "$eu_server" 22 "RU-to-EU SSH"
expect_blocked "$test_client" 22 "RU-to-test-client SSH"
expect_blocked "$other_host" "$outline_port" "RU-to-other-host Outline port"

echo "RU firewall allows the EU Outline transport and blocks representative direct egress"
