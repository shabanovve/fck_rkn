"""Checks executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
from fck_rkn.agents.ru_server import (
    CLIENT_CONTAINER_NAME,
    CLIENT_IMAGE,
    CLIENT_LOCAL_PORT,
    CLIENT_STATE_DIRECTORY,
    FORWARD_CONTAINER_NAME,
    FORWARD_LOCAL_PORT,
    PROBE_TARGET_URL,
    build_client_config,
    parse_access_url,
    run_remote_with_input,
)
from fck_rkn.config import TestEnvironmentSettings, load_config


logger = logging.getLogger(__name__)


CLIENT_CHECK_COMMAND = "\n".join(
    (
        "set -euo pipefail",
        "expected=$(cat)",
        f"container_name={CLIENT_CONTAINER_NAME}",
        'if [ "$(sudo docker inspect --format="{{.Config.Image}}" '
        '"$container_name" 2>/dev/null || true)" != ' f"{CLIENT_IMAGE} ] || "
        '[ "$(sudo docker inspect --format="{{.State.Status}}" '
        '"$container_name" 2>/dev/null || true)" != running ]; then',
        '  echo "Outline client container is not running with the expected image" >&2',
        "  exit 1",
        "fi",
        "actual=$(sudo cat " f"{CLIENT_STATE_DIRECTORY}/config.json)",
        'if [ "$actual" != "$expected" ]; then',
        '  echo "Outline client is not configured with the test key" >&2',
        "  exit 1",
        "fi",
        "if ! timeout 5 bash -c "
        f"'echo > /dev/tcp/127.0.0.1/{CLIENT_LOCAL_PORT}'; then",
        '  echo "Outline client local listener is unavailable" >&2',
        "  exit 1",
        "fi",
        'server_host=$(python3 -c "import json,sys; c=json.loads(sys.argv[1]); '
        'print(c[\'server\'], c[\'server_port\'])" "$expected")',
        "read -r server_host server_port <<<\"$server_host\"",
        "if ! timeout 10 bash -c "
        "'echo > /dev/tcp/\"$0\"/\"$1\"' \"$server_host\" \"$server_port\"; then",
        '  echo "EU Outline server is unreachable from the client" >&2',
        "  exit 1",
        "fi",
        "if ! status=$(curl --silent --output /dev/null --max-time 30 "
        "--write-out '%{http_code}' "
        f"--socks5-hostname 127.0.0.1:{CLIENT_LOCAL_PORT} {PROBE_TARGET_URL}); then",
        '  echo "HTTP request through the Outline client failed" >&2',
        "  exit 1",
        "fi",
        'case "$status" in',
        "  2??|3??) ;;",
        '  *) echo "Unexpected HTTP status through the Outline client: $status" >&2; exit 1 ;;',
        "esac",
        'echo "Outline client is running, uses the test key, and carries HTTP '
        'through the tunnel"',
    )
)


def check_outline_client(
    settings: TestEnvironmentSettings,
    access_url: str,
) -> int:
    """Verify the ru-server Outline client runs and is bound to the test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    return run_remote_with_input(
        settings,
        CLIENT_CHECK_COMMAND,
        build_client_config(method, password, host, port),
        "Checking the Outline client connection",
    )


FORWARD_CHECK_COMMAND = "\n".join(
    (
        "set -euo pipefail",
        f"container_name={FORWARD_CONTAINER_NAME}",
        'if [ "$(sudo docker inspect --format="{{.State.Status}}" '
        '"$container_name" 2>/dev/null || true)" != running ]; then',
        '  echo "Squid forward container is not running" >&2',
        "  exit 1",
        "fi",
        "headers=$(curl --silent --show-error --output /dev/null --dump-header - "
        f"--max-time 30 --proxy http://127.0.0.1:{FORWARD_LOCAL_PORT} "
        f"{PROBE_TARGET_URL})",
        "if ! printf '%s' \"$headers\" | grep -qiE '^(server|via|x-squid-error):.*squid'; then",
        '  echo "HTTP request through the forward did not reach EU Squid" >&2',
        "  exit 1",
        "fi",
        f'echo "ru-server localhost TCP/{FORWARD_LOCAL_PORT} forwards to EU Squid '
        'and carries HTTP"',
    )
)


def check_squid_forward(settings: TestEnvironmentSettings) -> int:
    """Verify RU localhost TCP/3128 reaches the real EU Squid and carries HTTP."""
    return run_remote_with_input(
        settings,
        FORWARD_CHECK_COMMAND,
        "",
        f"Checking the RU forward to EU Squid TCP/{SQUID_PORT}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server service checks.")
    parser.add_argument(
        "action",
        choices=("outline-client", "squid-forward"),
        help="Check to run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        settings = load_config()
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid test configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "squid-forward":
        return check_squid_forward(settings)

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    return check_outline_client(settings, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
