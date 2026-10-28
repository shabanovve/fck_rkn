#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
readarray -t values < <(printf '%s' "$config" | python3 -c '
import ipaddress, json, sys
c = json.load(sys.stdin)
ipaddress.ip_address(c["eu_server_address"])
ipaddress.ip_address(c["eu_outline_address"])
for key in ("eu_outline_port", "ssh_port"):
    port = c[key]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid " + key)
print(c["eu_server_address"])
print(c["eu_outline_address"])
print(c["eu_outline_port"])
print(c["ssh_port"])
')
eu_server=${values[0]}
outline_server=${values[1]}
outline_port=${values[2]}
ssh_port=${values[3]}
vpn_port=@VPN_PORT@
table=fck_rkn_ru

fail() {
  echo "$1" >&2
  exit 1
}

if ! rules=$(sudo nft list table inet "$table" 2>/dev/null); then
  fail "RU firewall table is not active"
fi
input_chain=$(printf '%s\n' "$rules" | awk '/chain input \{/{f=1} f{print} f&&/^  \}/{exit}')
output_chain=$(printf '%s\n' "$rules" | awk '/chain output \{/{f=1} f{print} f&&/^  \}/{exit}')
forward_chain=$(printf '%s\n' "$rules" | awk '/chain forward \{/{f=1} f{print} f&&/^  \}/{exit}')

[ -n "$input_chain" ] || fail "RU firewall input chain is missing"
[ -n "$output_chain" ] || fail "RU firewall output chain is missing"
[ -n "$forward_chain" ] || fail "RU firewall forward chain is missing"

printf '%s\n' "$input_chain" | grep -q 'hook input .*policy drop' || fail "RU firewall input chain is not default deny"
printf '%s\n' "$output_chain" | grep -q 'hook output .*policy drop' || fail "RU firewall output chain is not default deny"
printf '%s\n' "$forward_chain" | grep -q 'hook forward .*policy drop' || fail "RU firewall forward chain is not default deny"

printf '%s\n' "$input_chain" | grep -Fq "tcp dport $ssh_port accept" || fail "RU firewall does not allow SSH on port $ssh_port"
printf '%s\n' "$input_chain" | grep -Eq "meta l4proto \{ tcp, udp \} th dport $vpn_port accept" || fail "RU firewall does not allow the VPN port"
printf '%s\n' "$input_chain" | grep -Fq "tcp dport @API_PORT@ accept" || fail "RU firewall does not allow the Outline API port @API_PORT@"

for family in v4 v6; do
  printf '%s\n' "$input_chain" | grep -q "crawler-$family" || fail "RU firewall is missing the $family crawler drop rule"
done

printf '%s\n' "$output_chain" | grep -Eq "ip6? daddr $outline_server .*tcp dport $outline_port accept" || fail "RU firewall does not allow the VPN transport to EU"
printf '%s\n' "$output_chain" | grep -q "direct-eu-service" || fail "RU firewall is missing the direct-eu-service output drop rule"

echo "PASS: RU firewall policy is active and matches the expected rules"
