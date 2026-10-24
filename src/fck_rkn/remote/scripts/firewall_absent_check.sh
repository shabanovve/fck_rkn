#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "$1" >&2
  exit 1
}

if command -v nft >/dev/null 2>&1; then
  tables=$(sudo nft list tables)
  if grep -Fq 'fck_rkn_@HOST@' <<<"$tables"; then
    fail "The @HOST@ firewall table is still active"
  fi
fi
echo "PASS: No @HOST@ firewall table is active"
if sudo test -e @RULES_FILE@ || sudo test -e @UNIT_FILE@; then
  fail "The saved @HOST@ firewall rules or unit still exist"
fi
echo "PASS: The saved @HOST@ firewall rules and unit are removed"
if systemctl is-enabled --quiet @SERVICE_NAME@ 2>/dev/null ||
   systemctl is-active --quiet @SERVICE_NAME@ 2>/dev/null; then
  fail "The @HOST@ firewall service is still enabled or active"
fi
echo "PASS: The @HOST@ firewall service is neither enabled nor active"
if sudo test -e @BLACKLIST_SERVICE_FILE@ ||
   sudo test -e @BLACKLIST_TIMER_FILE@ ||
   sudo test -e @BLACKLIST_UPDATER_FILE@; then
  fail "The @HOST@ daily blacklist updater files still exist"
fi
if systemctl is-enabled --quiet @BLACKLIST_TIMER_NAME@ 2>/dev/null ||
   systemctl is-active --quiet @BLACKLIST_TIMER_NAME@ 2>/dev/null ||
   systemctl is-active --quiet @BLACKLIST_SERVICE_NAME@ 2>/dev/null; then
  fail "The @HOST@ daily blacklist updater is still enabled or active"
fi
echo "PASS: The @HOST@ daily blacklist updater is removed"
echo "No project firewall rules remain on the @HOST@ server"
