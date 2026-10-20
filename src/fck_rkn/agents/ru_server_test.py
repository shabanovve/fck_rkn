"""Checks executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
from fck_rkn.agents.outline import (
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_HEALTH_CHECK_COMMAND,
)
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
from fck_rkn.agent_config import load_agent_settings, ru_server_runner
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner


logger = logging.getLogger(__name__)


CLIENT_CHECK_COMMAND = render(
    "outline_client_check.sh",
    container_name=CLIENT_CONTAINER_NAME,
    image=CLIENT_IMAGE,
    state_directory=CLIENT_STATE_DIRECTORY,
    local_port=CLIENT_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def check_outline_client(
    runner: RemoteRunner,
    access_url: str,
) -> int:
    """Verify the ru-server Outline client runs and is bound to the test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    return run_remote_with_input(
        runner,
        CLIENT_CHECK_COMMAND,
        build_client_config(method, password, host, port),
        "Checking the Outline client connection",
    )


FORWARD_CHECK_COMMAND = render(
    "squid_forward_check.sh",
    container_name=FORWARD_CONTAINER_NAME,
    local_port=FORWARD_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def check_squid_forward(runner: RemoteRunner) -> int:
    """Verify RU localhost TCP/3128 reaches the real EU Squid and carries HTTP."""
    return run_remote_with_input(
        runner,
        FORWARD_CHECK_COMMAND,
        "",
        f"Checking the RU forward to EU Squid TCP/{SQUID_PORT}",
    )


TUNNEL_BLOCKED_CHECK_COMMAND = render(
    "tunnel_blocked_check.sh",
    container_name=FORWARD_CONTAINER_NAME,
    local_port=FORWARD_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def check_tunnel_blocked(runner: RemoteRunner) -> int:
    """Verify no traffic reaches EU Squid while the tunnel is down.

    Resumption after reconnection is verified by check_squid_forward.
    """
    return run_remote_with_input(
        runner,
        TUNNEL_BLOCKED_CHECK_COMMAND,
        "",
        "Checking that traffic is blocked while the tunnel is down",
    )


def check_outline_health(runner: RemoteRunner) -> int:
    """Check ru-server Outline container, authenticated API, and access port."""
    logger.info(
        "Checking Outline Server container, authorized API, and TCP access "
        "port %d on ru-server",
        OUTLINE_ACCESS_KEY_PORT,
    )
    return run_remote_with_input(
        runner,
        OUTLINE_HEALTH_CHECK_COMMAND,
        "",
        "Outline Server health check",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server service checks.")
    parser.add_argument(
        "action",
        choices=("outline-client", "outline-health", "squid-forward", "tunnel-blocked"),
        help="Check to run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        runner = ru_server_runner(load_agent_settings())
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "squid-forward":
        return check_squid_forward(runner)
    if args.action == "outline-health":
        return check_outline_health(runner)
    if args.action == "tunnel-blocked":
        return check_tunnel_blocked(runner)

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    return check_outline_client(runner, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
