#!/usr/bin/env bash
set -euo pipefail
host=@HOST@
rules_file=@RULES_FILE@
lock_file=@LOCK_FILE@
updater=/usr/local/libexec/fck-rkn/rkn_blacklist.py
service_name=fck-rkn-@HOST@-blacklist-update.service
timer_name=fck-rkn-@HOST@-blacklist-update.timer

sudo install -d -m 755 /usr/local/libexec/fck-rkn
sudo tee "$updater" >/dev/null <<'PYTHON'
#include agents/rkn_blacklist.py
PYTHON
sudo chmod 755 "$updater"

sudo tee "/etc/systemd/system/$service_name" >/dev/null <<UNIT
[Unit]
Description=Refresh the RKN crawler firewall blacklist on $host
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $updater --host $host --rules-file $rules_file --lock-file $lock_file
UNIT

sudo tee "/etc/systemd/system/$timer_name" >/dev/null <<UNIT
[Unit]
Description=Daily RKN crawler firewall blacklist refresh on $host

[Timer]
OnCalendar=*-*-* 03:17:00
RandomizedDelaySec=1h
Persistent=true
Unit=$service_name

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "$timer_name"
sudo systemctl start "$service_name"
