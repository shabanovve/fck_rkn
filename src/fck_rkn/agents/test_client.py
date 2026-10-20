"""Actions executed remotely on the test-client VM."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
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
SQUID_TUNNEL_CONTAINER_NAME = "fck-rkn-test-client-squid"
SQUID_TUNNEL_STATE_DIRECTORY = "/opt/fck-rkn-test-client-squid"
SQUID_TUNNEL_LOCAL_PORT = SQUID_PORT


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
        stream_output=True,
    )
    if result == 0:
        logger.info("test-client connection to RU Outline is deployed")
    return result


SQUID_TUNNEL_CHECK_COMMAND = render(
    "squid_tunnel_check.sh",
    container_name=SQUID_TUNNEL_CONTAINER_NAME,
    local_port=SQUID_TUNNEL_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def check_squid_tunnel(runner: RemoteRunner) -> int:
    """Verify localhost TCP/3128 on test-client reaches EU Squid through RU."""
    return run_remote_with_input(
        runner,
        SQUID_TUNNEL_CHECK_COMMAND,
        "",
        "Checking the test-client Squid tunnel listener",
    )


HTTP_REQUEST_COMMAND = render(
    "client_http_request.sh",
    local_port=SQUID_TUNNEL_LOCAL_PORT,
    target_url=PROBE_TARGET_URL,
)


def request_youtube(runner: RemoteRunner) -> int:
    """Send an HTTP request to youtube.com through client-to-RU-to-EU Squid.

    Succeeds whenever an HTTP response arrives; judging the status is a
    separate assertion.
    """
    return run_remote_with_input(
        runner,
        HTTP_REQUEST_COMMAND,
        "",
        "HTTP request to youtube.com through the test-client proxy path",
    )


def build_squid_tunnel_config(
    method: str, password: str, host: str, port: int, squid_address: str
) -> str:
    """Return the ss-tunnel config exposing EU Squid on test-client localhost.

    Outline provides a local proxy, not a router or layer-3 gateway, so the
    approved listener is a TCP forward from 127.0.0.1 through RU Outline to
    the EU Squid private address.
    """
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": SQUID_TUNNEL_LOCAL_PORT,
            "tunnel_address": f"{squid_address}:{SQUID_PORT}",
        }
    )


def deploy_squid_tunnel(
    runner: RemoteRunner, access_url: str, squid_address: str
) -> int:
    """Forward test-client localhost TCP/3128 to EU Squid through RU Outline."""
    if not squid_address.strip():
        logger.error("The EU Squid address must not be empty")
        return 1
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    command = render(
        "deploy_container.sh",
        container_name=SQUID_TUNNEL_CONTAINER_NAME,
        image=CLIENT_IMAGE,
        state_directory=SQUID_TUNNEL_STATE_DIRECTORY,
        entrypoint="ss-tunnel",
        title="Squid tunnel",
    )
    result = execute(
        runner,
        command,
        "Deploying the EU Squid tunnel on test-client",
        CLIENT_DEPLOY_TIMEOUT_SECONDS,
        stdin=build_squid_tunnel_config(
            method, password, host, port, squid_address.strip()
        ),
        stream_output=True,
    )
    if result == 0:
        logger.info("test-client EU Squid tunnel is deployed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run test-client actions.")
    parser.add_argument("action", choices=("connect-outline", "check-outline", "connect-squid", "check-squid", "request-youtube"),
        help="Action to run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        settings = load_agent_settings()
        runner = test_client_runner(settings)
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)", type(error).__name__
        )
        return 2

    if args.action == "check-squid":
        return check_squid_tunnel(runner)
    if args.action == "request-youtube":
        return request_youtube(runner)

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    if args.action == "connect-squid":
        return deploy_squid_tunnel(
            runner, access_url, settings.EU_SERVER_PRIVATE_ADDRESS
        )
    if args.action == "check-outline":
        return check_outline_connection(runner, access_url)
    return connect_to_outline(runner, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
