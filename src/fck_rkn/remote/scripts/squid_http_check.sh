#!/usr/bin/env bash
python3 - @PORT@ <<'PY'
#include squid_http_check.py
PY
