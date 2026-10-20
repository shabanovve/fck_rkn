"""Startup checks executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import (
    MTPROXY_CONTAINER_NAME,
    MTPROXY_PORT,
    MTPROXY_STATE_DIRECTORY,
    OUTLINE_ACCESS_KEY_PORT,
    SQUID_CONTAINER_NAME,
    SQUID_PORT,
)
from fck_rkn.agents.outline import OUTLINE_HEALTH_CHECK_COMMAND
from fck_rkn.agent_config import eu_server_runner, load_agent_settings
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
SSH_COMMAND_TIMEOUT_SECONDS = 90
SQUID_STARTUP_CHECK_COMMAND = render(
    "squid_startup_check.sh",
    container_name=SQUID_CONTAINER_NAME,
    port=SQUID_PORT,
)
SQUID_HTTP_CHECK_COMMAND = render("squid_http_check.sh", port=SQUID_PORT)
MTPROXY_CHECK_COMMAND = render(
    "mtproxy_check.sh",
    container_name=MTPROXY_CONTAINER_NAME,
    port=MTPROXY_PORT,
    state_directory=MTPROXY_STATE_DIRECTORY,
)


def check_squid_startup(runner: RemoteRunner) -> int:
    """Check Squid container health and its local TCP listener."""
    logger.info("Checking Squid container and TCP port %d on eu-server", SQUID_PORT)
    result = _run_check(runner, SQUID_STARTUP_CHECK_COMMAND, "Squid startup check")
    if result == 0:
        logger.info(
            "Squid container is running and TCP port %d accepts connections",
            SQUID_PORT,
        )
    return result


def check_squid_http(runner: RemoteRunner) -> int:
    """Check an HTTP request to youtube.com through Squid on eu-server."""
    logger.info("Checking HTTP GET youtube.com through Squid on eu-server")
    result = _run_check(
        runner, SQUID_HTTP_CHECK_COMMAND, "HTTP request through Squid"
    )
    if result == 0:
        logger.info("HTTP GET youtube.com through Squid succeeded")
    return result


def check_mtproxy(runner: RemoteRunner) -> int:
    """Check MTProxy container, TCP listener and secret file without printing it."""
    logger.info("Checking MTProxy container and TCP port %d on eu-server", MTPROXY_PORT)
    result = _run_check(runner, MTPROXY_CHECK_COMMAND, "MTProxy check")
    if result == 0:
        logger.info("MTProxy is running and accepts connections")
    return result


def check_outline_health(runner: RemoteRunner) -> int:
    """Check Outline container, authenticated API, and access port."""
    logger.info(
        "Checking Outline Server container, authorized API, and TCP access "
        "port %d on eu-server",
        OUTLINE_ACCESS_KEY_PORT,
    )
    result = _run_check(
        runner, OUTLINE_HEALTH_CHECK_COMMAND, "Outline Server health check"
    )
    if result == 0:
        logger.info("Outline Server authorized endpoint is healthy")
    return result


def _run_check(runner: RemoteRunner, command: str, description: str) -> int:
    return execute(
        runner,
        command,
        description,
        SSH_COMMAND_TIMEOUT_SECONDS,
        print_output=True,
        print_stderr=True,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(
        description="Run eu-server service checks."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("startup", "http", "outline", "mtproxy"),
        default="startup",
        help="Check to run (default: startup)",
    )
    args = parser.parse_args(argv)

    try:
        runner = eu_server_runner(load_agent_settings())
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "http":
        return check_squid_http(runner)
    if args.action == "mtproxy":
        return check_mtproxy(runner)
    if args.action == "outline":
        return check_outline_health(runner)
    return check_squid_startup(runner)


if __name__ == "__main__":
    raise SystemExit(main())
