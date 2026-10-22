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

output_chain=$(sudo nft list chain inet fck_rkn_ru output)
if ! printf '%s\n' "$output_chain" | grep -q 'policy accept'; then
  echo "RU firewall output chain is not in log-only mode" >&2
  exit 1
fi
if ! sudo nft list table inet fck_rkn_ru >/dev/null; then
  echo "RU firewall table is not active" >&2
  exit 1
fi
for log_prefix in \
  "fck-rkn ru input crawler-v4:" \
  "fck-rkn ru input crawler-v6:" \
  "fck-rkn ru input test-client:" \
  "fck-rkn ru output direct-eu-service:" \
  "fck-rkn ru output test-client:" \
  "fck-rkn ru output default-policy:"; do
  if ! sudo nft list table inet fck_rkn_ru | grep -Fq "$log_prefix"; then
    echo "RU firewall is missing a log-only rule: $log_prefix" >&2
    exit 1
  fi
done
if printf '%s\n' "$output_chain" | grep -Eq '\bdrop\b'; then
  echo "RU firewall output chain still contains a drop verdict" >&2
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

expect_reachable "$outline_server" "$outline_port" "RU-to-EU Outline transport"

echo "RU firewall is in log-only mode; the EU Outline transport is reachable"
