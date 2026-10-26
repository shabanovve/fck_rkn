#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
ipaddress.ip_address(c["ru_server_address"])
port = c["ssh_port"]
if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
    raise ValueError("invalid ssh_port")
print(c["ru_server_address"])
print(port)
')
ru_address=${values[0]}
ssh_port=${values[1]}
vpn_port=@VPN_PORT@
table=fck_rkn_eu

fail() {
  echo "$1" >&2
  exit 1
}

# Read-only: only lists the active ruleset; no namespaces, routes or probe rules.
if ! rules=$(sudo nft list table inet "$table" 2>/dev/null); then
  fail "EU firewall table is not active"
fi
input_chain=$(printf '%s\n' "$rules" | awk '/chain input \{/{f=1} f{print} f&&/^  \}/{exit}')
[ -n "$input_chain" ] || fail "EU firewall input chain is missing"
printf '%s\n' "$rules" | grep -q 'hook output' &&
  fail "EU firewall must not filter outgoing traffic"
printf '%s\n' "$input_chain" | grep -q 'hook input .*policy drop' ||
  fail "EU firewall input chain is not default deny"
printf '%s\n' "$input_chain" | grep -Fq "tcp dport $ssh_port accept" ||
  fail "EU firewall does not allow SSH on port $ssh_port"
printf '%s\n' "$input_chain" |
  grep -Eq "ip6? saddr $ru_address .*th dport $vpn_port accept" ||
  fail "EU firewall does not allow the RU server to reach the VPN port"
for family in v4 v6; do
  printf '%s\n' "$input_chain" | grep -q "crawler-$family" ||
    fail "EU firewall is missing the $family crawler drop rule"
done
for set_name in rkn_ipv4 rkn_ipv6; do
  printf '%s\n' "$rules" |
    awk -v name="$set_name" '$1=="set"&&$2==name{f=1} f&&/elements = \{/{found=1} f&&/^  \}/{exit} END{exit !found}' ||
    fail "EU firewall set $set_name is empty or missing"
done
crawler_line=$(printf '%s\n' "$input_chain" | grep -n 'crawler-v4' | head -1 | cut -d: -f1)
vpn_line=$(printf '%s\n' "$input_chain" | grep -nE "th dport $vpn_port accept" | head -1 | cut -d: -f1)
[ "$crawler_line" -lt "$vpn_line" ] ||
  fail "EU firewall crawler drop rule must precede the VPN accept rule"
printf '%s\n' "$rules" | grep -q 'direct-service' ||
  fail "EU firewall is missing the direct-service forward drop rule"
echo "PASS: EU firewall policy is active and matches the expected rules"
