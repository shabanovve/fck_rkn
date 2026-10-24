#!/usr/bin/env bash
set -euo pipefail
host=@HOST@
table_name=fck_rkn_@HOST@
rules_file=@RULES_FILE@
service_name=fck-rkn-@HOST@-blacklist-update.service
timer_name=fck-rkn-@HOST@-blacklist-update.timer

if ! sudo systemctl is-enabled --quiet "$timer_name" ||
   ! sudo systemctl is-active --quiet "$timer_name"; then
  echo "RKN blacklist update timer is not enabled and active" >&2
  exit 1
fi
if [ "$(sudo systemctl show --property=Result --value "$service_name")" != success ]; then
  echo "The latest RKN blacklist update did not succeed" >&2
  exit 1
fi
for set_name in rkn_ipv4 rkn_ipv6; do
  if ! grep -Eq "^  set $set_name \\{" "$rules_file"; then
    echo "Saved firewall rules are missing $set_name" >&2
    exit 1
  fi
  active_set=$(sudo nft list set inet "$table_name" "$set_name")
  if ! grep -Eq 'elements = \{[[:space:]]*[^}]+[[:space:]]*\}' \
      <<<"$active_set"; then
    echo "Active firewall set $set_name is empty or unavailable" >&2
    exit 1
  fi
done
echo "RKN blacklist updater is active and both crawler sets are populated"
