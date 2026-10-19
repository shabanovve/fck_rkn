"""Checks executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
from fck_rkn.agents.ru_server import (
    PROBE_LOCAL_PORT,
    PROBE_TARGET_URL,
    build_tunnel_command,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server service checks.")
    parser.add_argument(
        "action",
        choices=("outline-squid-route",),
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
    return check_outline_squid_route(settings, sys.stdin.readline())


if __name__ == "__main__":
    raise SystemExit(main())
