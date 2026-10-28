#!/usr/bin/env bash
set -euo pipefail

api_url="$(sudo sed -n 's/^apiUrl://p' @STATE_DIRECTORY@/access.txt)"
cert_sha256="$(sudo sed -n 's/^certSha256://p' @STATE_DIRECTORY@/access.txt)"

if [ -z "$api_url" ] || [ -z "$cert_sha256" ]; then
    echo "Could not read Outline Manager credentials" >&2
    exit 1
fi

# The configured address may be a private IP (e.g. 10.x.x.x). We replace it
# with the public host address if we are requested to.
if [ -n "@PUBLIC_ADDRESS@" ]; then
    api_url="$(printf '%s' "$api_url" | sed -E "s#^(https?://)\[?[^/]+\]?:([0-9]+)/#\1@PUBLIC_ADDRESS@:\2/#")"
fi

cat <<JSON
{"apiUrl":"$api_url","certSha256":"$cert_sha256"}
JSON
