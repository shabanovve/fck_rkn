#!/usr/bin/env bash
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '%s' '@INSTALLER_BASE64@' | base64 --decode > "$work/apply-firewall.sh"
chmod 700 "$work/apply-firewall.sh"

rules_file=@RULES_FILE@
service_name=@SERVICE_NAME@
if ! sudo test -s "$rules_file" ||
   ! sudo systemctl is-active --quiet "$service_name"; then
  echo "Cannot test rollback without an installed, active @HOST@ firewall" >&2
  exit 1
fi
sudo nft list table inet fck_rkn_@HOST@ > "$work/active-before"
sudo sha256sum "$rules_file" | awk '{print $1}' > "$work/saved-before"
sudo systemctl is-active "$service_name" > "$work/service-before"

cat > "$work/invalid.nft" <<'RULES'
destroy table inet fck_rkn_@HOST@
table inet fck_rkn_@HOST@ {
  set rkn_ipv4 { type ipv4_addr; flags interval; elements = { 192.0.2.0/24 } }
  set rkn_ipv6 { type ipv6_addr; flags interval; elements = { 2001:db8::/32 } }
  this is not valid nftables syntax
}
RULES
if printf '%s' "$(cat "$work/invalid.nft")" |
  bash "$work/apply-firewall.sh" > "$work/update-output" 2>&1; then
  echo "Invalid firewall update unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -q "nftables rejected the firewall candidate" "$work/update-output"; then
  echo "Firewall updater failed without reporting candidate validation failure" >&2
  exit 1
fi

sudo sha256sum "$rules_file" | awk '{print $1}' > "$work/saved-after"
sudo nft list table inet fck_rkn_@HOST@ > "$work/active-after"
sudo systemctl is-active "$service_name" > "$work/service-after"
if ! cmp -s "$work/saved-before" "$work/saved-after" ||
   ! cmp -s "$work/active-before" "$work/active-after" ||
   ! cmp -s "$work/service-before" "$work/service-after"; then
  echo "Rejected firewall update changed the saved or active policy" >&2
  exit 1
fi

mkdir -p "$work/initial/run/lock"
python3 - "$work/apply-firewall.sh" "$work/initial" <<'PY'
from pathlib import Path
import sys

installer = Path(sys.argv[1])
root = sys.argv[2]
content = installer.read_text()
content = content.replace("/etc/fck-rkn/@HOST@-firewall.nft", f"{root}/rules.nft")
content = content.replace(
    "/etc/systemd/system/fck-rkn-@HOST@-firewall.service", f"{root}/firewall.service"
)
content = content.replace(
    "/run/lock/fck-rkn-@HOST@-firewall.lock", f"{root}/run/lock/firewall.lock"
)
Path(f"{root}/initial-apply-firewall.sh").write_text(content)
PY
if printf '%s' "$(cat "$work/invalid.nft")" |
  bash "$work/initial/initial-apply-firewall.sh" > "$work/initial-output" 2>&1; then
  echo "Invalid initial firewall configuration unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -q "nftables rejected the firewall candidate" "$work/initial-output" ||
   [ -e "$work/initial/rules.nft" ] ||
   [ -e "$work/initial/firewall.service" ]; then
  echo "Invalid initial firewall configuration was not rejected before installation" >&2
  exit 1
fi
echo "Rejected @HOST@ firewall updates preserved active and saved rules and installed no partial initial policy"
