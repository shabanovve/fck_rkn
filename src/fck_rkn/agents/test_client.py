"""Actions executed remotely on the test-client VM."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import MTPROXY_PORT, SQUID_PORT
from fck_rkn.agent_config import load_agent_settings, test_client_runner
from fck_rkn.remote import render
from fck_rkn.agents.ru_server import (
    CLIENT_DEPLOY_TIMEOUT_SECONDS,
    CLIENT_IMAGE,
    CLIENT_LOCAL_PORT,
    PROBE_TARGET_URL,
    build_client_config,
    parse_access_url,
    run_remote_with_input,
)
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
CONNECTION_CONTAINER_NAME = "fck-rkn-test-client-outline"
CONNECTION_STATE_DIRECTORY = "/opt/fck-rkn-test-client-outline"
SQUID_TUNNEL_CONTAINER_NAME = "fck-rkn-test-client-squid"
SQUID_TUNNEL_STATE_DIRECTORY = "/opt/fck-rkn-test-client-squid"
SQUID_TUNNEL_LOCAL_PORT = SQUID_PORT
MTPROXY_TUNNEL_CONTAINER_NAME = "fck-rkn-test-client-mtproxy"
MTPROXY_TUNNEL_STATE_DIRECTORY = "/opt/fck-rkn-test-client-mtproxy"
MTPROXY_TUNNEL_LOCAL_PORT = MTPROXY_PORT


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
    """Request youtube.com through client-to-RU-to-EU Squid and assert the result.

    Success: HTTP status 2xx or 3xx and a Squid Via/Server header. A missing
    response, another status, or a response not from Squid fails.
    """
    return run_remote_with_input(
        runner,
        HTTP_REQUEST_COMMAND,
        "",
        "HTTP request to youtube.com through the test-client proxy path",
    )


def build_squid_tunnel_config(
    method: str, password: str, host: str, port: int
) -> str:
    """Return the config forwarding through RU to the local Squid forward.

    Outline provides a local proxy, not a router or layer-3 gateway, so the
    approved path is a TCP forward from test-client through RU Outline to
    RU's localhost listener, which itself forwards to EU through EU Outline.
    """
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": SQUID_TUNNEL_LOCAL_PORT,
            "tunnel_address": f"127.0.0.1:{SQUID_PORT}",
        }
    )


def deploy_squid_tunnel(runner: RemoteRunner, access_url: str) -> int:
    """Forward test-client localhost TCP/3128 through RU to EU Squid."""
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
        stdin=build_squid_tunnel_config(method, password, host, port),
        stream_output=True,
    )
    if result == 0:
        logger.info("test-client EU Squid tunnel is deployed")
    return result


def build_mtproxy_tunnel_config(
    method: str, password: str, host: str, port: int
) -> str:
    """Return the ss-tunnel config to reach RU's localhost MTProxy forward."""
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": MTPROXY_TUNNEL_LOCAL_PORT,
            "tunnel_address": f"127.0.0.1:{MTPROXY_PORT}",
        }
    )


def deploy_mtproxy_tunnel(runner: RemoteRunner, access_url: str) -> int:
    """Forward test-client localhost TCP/8443 through RU to EU MTProxy."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    command = render(
        "deploy_container.sh",
        container_name=MTPROXY_TUNNEL_CONTAINER_NAME,
        image=CLIENT_IMAGE,
        state_directory=MTPROXY_TUNNEL_STATE_DIRECTORY,
        entrypoint="ss-tunnel",
        title="MTProxy tunnel",
    )
    result = execute(
        runner,
        command,
        "Deploying the MTProxy tunnel on test-client",
        CLIENT_DEPLOY_TIMEOUT_SECONDS,
        stdin=build_mtproxy_tunnel_config(method, password, host, port),
        stream_output=True,
    )
    if result == 0:
        logger.info("test-client MTProxy tunnel is deployed")
    return result


MTPROXY_TUNNEL_CHECK_COMMAND = render(
    "mtproxy_client_tunnel_check.sh",
    container_name=MTPROXY_TUNNEL_CONTAINER_NAME,
    state_directory=MTPROXY_TUNNEL_STATE_DIRECTORY,
    local_port=MTPROXY_TUNNEL_LOCAL_PORT,
)


def check_mtproxy_tunnel(runner: RemoteRunner) -> int:
    """Verify test-client TCP/8443 reaches its RU Outline forward."""
    return run_remote_with_input(
        runner,
        MTPROXY_TUNNEL_CHECK_COMMAND,
        "",
        "Checking the test-client MTProxy tunnel listener",
    )


def check_restricted_service_paths(
    runner: RemoteRunner,
    ru_server_address: str,
    eu_server_address: str | None,
) -> int:
    """Verify direct client service access is blocked on selected servers."""
    try:
        ru_address = ipaddress.ip_address(ru_server_address).compressed
        eu_address = (
            ipaddress.ip_address(eu_server_address).compressed
            if eu_server_address is not None
            else ""
        )
    except ValueError as error:
        logger.error("Firewall test server addresses must be IP addresses")
        return 1
    if eu_address and ru_address == eu_address:
        logger.error("RU and EU firewall test addresses must be distinct")
        return 1
    command = render(
        "test_client_firewall_check.sh",
        ru_server_address=ru_address,
        eu_server_address=eu_address,
    )
    return run_remote_with_input(
        runner,
        command,
        "",
        "Checking that test-client cannot connect directly to selected services",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run test-client actions.")
    parser.add_argument(
        "action",
        choices=(
            "connect-outline",
            "check-outline",
            "connect-squid",
            "check-squid",
            "connect-mtproxy",
            "check-mtproxy",
            "check-firewall-paths",
            "request-youtube",
        ),
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
    if args.action == "check-mtproxy":
        return check_mtproxy_tunnel(runner)
    if args.action == "check-firewall-paths":
        return check_restricted_service_paths(
            runner,
            settings.RU_SERVER_PRIVATE_ADDRESS,
            settings.EU_SERVER_ADDRESS,
        )
    if args.action == "request-youtube":
        return request_youtube(runner)

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    if args.action == "connect-squid":
        return deploy_squid_tunnel(runner, access_url)
    if args.action == "connect-mtproxy":
        return deploy_mtproxy_tunnel(runner, access_url)
    if args.action == "check-outline":
        return check_outline_connection(runner, access_url)
    return connect_to_outline(runner, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
