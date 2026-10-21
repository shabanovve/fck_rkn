#!/usr/bin/env bash
set -euo pipefail

candidate=$(mktemp)
trap 'rm -f "$candidate"' EXIT
cat > "$candidate"

sudo flock -n @LOCK_FILE@ bash -s -- "$candidate" <<'ROOT_SCRIPT'
set -euo pipefail
candidate=$1
rules_file=@RULES_FILE@
unit_file=@UNIT_FILE@
service_name=fck-rkn-ru-firewall.service

if ! command -v nft >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nftables
fi

if ! grep -q 'set rkn_ipv4' "$candidate" ||
   ! grep -q 'set rkn_ipv6' "$candidate" ||
   ! grep -Eq 'elements = \{[[:space:]]*[^}]+[[:space:]]*\}' "$candidate"; then
  echo "Firewall candidate is missing non-empty RKN address sets" >&2
  exit 1
fi
if ! nft --check --file "$candidate"; then
  echo "nftables rejected the firewall candidate; active rules are unchanged" >&2
  exit 1
fi

install -d -m 700 "$(dirname "$rules_file")" "$(dirname "$unit_file")"
backup_dir=$(mktemp -d "$(dirname "$rules_file")/.ru-firewall-backup.XXXXXX")
had_rules=false
had_unit=false
if [ -f "$rules_file" ]; then
  cp -p "$rules_file" "$backup_dir/rules"
  had_rules=true
fi
if [ -f "$unit_file" ]; then
  cp -p "$unit_file" "$backup_dir/unit"
  had_unit=true
fi

cat > "$backup_dir/unit.new" <<UNIT
[Unit]
Description=fck-rkn RU guest firewall
After=network-pre.target nftables.service
Before=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f $rules_file
ExecReload=/usr/sbin/nft -f $rules_file

[Install]
WantedBy=multi-user.target
UNIT
install -m 600 "$candidate" "$backup_dir/rules.new"
mv -f "$backup_dir/rules.new" "$rules_file"
install -m 644 "$backup_dir/unit.new" "$unit_file"

restore_previous() {
  local rollback_failed=false
  if [ "$had_rules" = true ]; then
    mv -f "$backup_dir/rules" "$rules_file" || rollback_failed=true
  else
    rm -f "$rules_file" || rollback_failed=true
  fi
  if [ "$had_unit" = true ]; then
    mv -f "$backup_dir/unit" "$unit_file" || rollback_failed=true
  else
    rm -f "$unit_file" || rollback_failed=true
  fi
  systemctl daemon-reload || rollback_failed=true
  if [ "$had_rules" = true ]; then
    nft -f "$rules_file" || rollback_failed=true
  else
    printf 'destroy table inet fck_rkn_ru\n' > "$backup_dir/remove.nft"
    nft -f "$backup_dir/remove.nft" || rollback_failed=true
    systemctl disable --now "$service_name" >/dev/null 2>&1 || true
  fi
  if [ "$rollback_failed" = true ]; then
    echo "Firewall update failed and rollback could not be fully confirmed" >&2
  else
    rm -f "$backup_dir/rules.new" "$backup_dir/unit.new" "$backup_dir/remove.nft" || true
    rmdir "$backup_dir" || true
  fi
}

if ! systemctl daemon-reload ||
   ! systemctl enable "$service_name" ||
   ! systemctl reload-or-restart "$service_name"; then
  echo "Could not activate the RU firewall candidate; restoring the previous configuration" >&2
  restore_previous
  exit 1
fi

rm -f "$backup_dir/rules" "$backup_dir/unit" "$backup_dir/unit.new" || true
rmdir "$backup_dir" || true
echo "RU firewall rules installed"
ROOT_SCRIPT
