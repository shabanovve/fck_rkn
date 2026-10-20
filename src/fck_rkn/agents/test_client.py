"""Actions executed remotely on the test-client VM."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agent_config import load_agent_settings, test_client_runner
from fck_rkn.agents.ru_server import (
    CLIENT_DEPLOY_TIMEOUT_SECONDS,
    CLIENT_IMAGE,
    CLIENT_LOCAL_PORT,
    PROBE_TARGET_URL,
    build_client_config,
    parse_access_url,
    run_remote_with_input,
)
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
CONNECTION_CONTAINER_NAME = "fck-rkn-test-client-outline"
CONNECTION_STATE_DIRECTORY = "/opt/fck-rkn-test-client-outline"


def build_connection_deploy_command() -> str:
    """Return a remote script that starts the persistent RU Outline connection.

    The configuration (including the key) arrives on stdin and is stored in a
    root-only file; it is never part of the command line.
    """
    return render(
        "deploy_container.sh",
        container_name=CONNECTION_CONTAINER_NAME,
        image=CLIENT_IMAGE,
        state_directory=CONNECTION_STATE_DIRECTORY,
        entrypoint="ss-local",
        title="Outline connection",
    )


CONNECTION_CHECK_COMMAND = render(
    "outline_client_check.sh",
    container_name=CONNECTION_CONTAINER_NAME,
    image=CLIENT_IMAGE,
    state_directory=CONNECTION_STATE_DIRECTORY,
    local_port=CLIENT_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def check_outline_connection(runner: RemoteRunner, access_url: str) -> int:
    """Verify test-client is connected to RU Outline with the test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    return run_remote_with_input(
        runner,
        CONNECTION_CHECK_COMMAND,
        build_client_config(method, password, host, port),
        "Checking the test-client connection to RU Outline",
    )


def connect_to_outline(runner: RemoteRunner, access_url: str) -> int:
    """Run the Outline client (ss-local) on test-client with the RU test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    logger.info("Connecting test-client to RU Outline in Docker")
    result = execute(
        runner,
        build_connection_deploy_command(),
        "Connecting test-client to RU Outline in Docker",
        CLIENT_DEPLOY_TIMEOUT_SECONDS,
        stdin=build_client_config(method, password, host, port),
        print_output=True,
    )
    if result == 0:
        logger.info("test-client connection to RU Outline is deployed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run test-client actions.")
    parser.add_argument("action", choices=("connect-outline", "check-outline"), help="Action to run")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        runner = test_client_runner(load_agent_settings())
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)", type(error).__name__
        )
        return 2

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    if args.action == "check-outline":
        return check_outline_connection(runner, access_url)
    return connect_to_outline(runner, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
