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
    PROBE_LOCAL_PORT,
    PROBE_TARGET_URL,
    build_client_config,
    build_tunnel_command,
    parse_access_url,
    run_remote_with_input,
    run_remote_with_key,
)
from fck_rkn.config import TestEnvironmentSettings, load_config


logger = logging.getLogger(__name__)
# Squid identifies itself in the Server/Via/X-Squid-Error headers, so a match
# proves the response came from the EU Squid and not from any other listener.
SQUID_ROUTE_CHECK_LINES = (
    "headers=$(curl --silent --show-error --output /dev/null --dump-header - "
    f"--max-time 30 --proxy http://127.0.0.1:{PROBE_LOCAL_PORT} {PROBE_TARGET_URL})",
    "if ! printf '%s' \"$headers\" | grep -qiE '^(server|via|x-squid-error):.*squid'; then",
    '  echo "Response did not come from EU Squid through the Outline tunnel" >&2',
    "  exit 1",
    "fi",
    'echo "Real route ru-server -> EU Outline -> EU Squid TCP/' f'{SQUID_PORT}'
    ' is working"',
)


def check_outline_squid_route(
    settings: TestEnvironmentSettings,
    access_url: str,
) -> int:
    """Verify the real Outline route from ru-server to EU Squid."""
    return run_remote_with_key(
        settings,
        access_url,
        build_tunnel_command(SQUID_ROUTE_CHECK_LINES),
        f"Checking the Outline route to EU Squid TCP/{SQUID_PORT}",
    )


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
        'echo "Outline client is running, uses the test key, and reaches EU Outline"',
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server service checks.")
    parser.add_argument(
        "action",
        choices=("outline-squid-route", "outline-client"),
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

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    if args.action == "outline-client":
        return check_outline_client(settings, access_url)
    return check_outline_squid_route(settings, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
