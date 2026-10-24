#!/usr/bin/env bash
set -euo pipefail

sudo flock -w 60 @LOCK_FILE@ bash -s <<'ROOT_SCRIPT'
set -euo pipefail
systemctl disable --now @SERVICE_NAME@ >/dev/null 2>&1 || true
rm -f @UNIT_FILE@ @RULES_FILE@
systemctl daemon-reload
systemctl reset-failed @SERVICE_NAME@ >/dev/null 2>&1 || true
if command -v nft >/dev/null 2>&1; then
  printf 'destroy table inet fck_rkn_@HOST@\n' | nft -f -
fi
echo "@HOST@ firewall removed"
ROOT_SCRIPT
