#!/usr/bin/env bash
set -euo pipefail
sudo python3 - @USER_NAME@ @STATE_DIRECTORY@ <<'PY'
#include create_outline_user.py
PY
