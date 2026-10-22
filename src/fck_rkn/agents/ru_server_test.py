"""Checks executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
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
    MTPROXY_FORWARD_CONTAINER_NAME,
    MTPROXY_FORWARD_LOCAL_PORT,
    MTPROXY_FORWARD_STATE_DIRECTORY,
    PROBE_TARGET_URL,
    build_firewall_apply_command,
    build_client_config,
    parse_access_url,
    run_remote_with_input,
)
from fck_rkn.agent_config import load_agent_settings, ru_server_runner
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
FIREWALL_ROLLBACK_TIMEOUT_SECONDS = 120


def _validated_address(address: str, name: str) -> str:
    try:
        return ipaddress.ip_address(address).compressed
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an IP address") from error


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


MTPROXY_FORWARD_CHECK_COMMAND = render(
    "mtproxy_forward_check.sh",
    container_name=MTPROXY_FORWARD_CONTAINER_NAME,
    state_directory=MTPROXY_FORWARD_STATE_DIRECTORY,
    local_port=MTPROXY_FORWARD_LOCAL_PORT,
)


def check_mtproxy_forward(runner: RemoteRunner) -> int:
    """Verify RU localhost TCP/8443 forwards to EU MTProxy only through Outline."""
    return run_remote_with_input(
        runner,
        MTPROXY_FORWARD_CHECK_COMMAND,
        "",
        "Checking the RU forward to EU MTProxy",
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


MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND = render(
    "mtproxy_forward_blocked_check.sh",
    container_name=MTPROXY_FORWARD_CONTAINER_NAME,
    local_port=MTPROXY_FORWARD_LOCAL_PORT,
)


def check_mtproxy_forward_blocked(runner: RemoteRunner) -> int:
    """Verify the MTProxy forward accepts no traffic while the tunnel is down."""
    return run_remote_with_input(
        runner,
        MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND,
        "",
        "Checking that MTProxy traffic is blocked while the tunnel is down",
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


def build_firewall_egress_check_command() -> str:
    """Create a remote check for RU's active egress firewall policy."""
    return render("ru_firewall_egress_check.sh")


def check_firewall_egress(runner: RemoteRunner, config_json: str) -> int:
    """Verify log-only RU egress rules and the approved Outline transport."""
    try:
        config = json.loads(config_json)
        if not isinstance(config, dict):
            raise ValueError("firewall test configuration must be an object")
        for key in (
            "eu_server_address",
            "eu_outline_address",
            "test_client_address",
            "other_host_address",
        ):
            config[key] = _validated_address(config.get(key), key)
        if config["other_host_address"] in {
            config["eu_server_address"],
            config["eu_outline_address"],
            config["test_client_address"],
        }:
            raise ValueError("other_host_address must be a distinct host")
        port = config.get("eu_outline_port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("eu_outline_port must be between 1 and 65535")
        remote_config = json.dumps(config)
    except (TypeError, ValueError, KeyError) as error:
        logger.error("Invalid RU firewall test configuration: %s", error)
        return 1
    return run_remote_with_input(
        runner,
        build_firewall_egress_check_command(),
        remote_config,
        "Checking the RU firewall's allowed and denied egress paths",
    )


def build_firewall_rollback_check_command() -> str:
    """Return a test that exercises the same transactional firewall installer."""
    installer = base64.b64encode(
        build_firewall_apply_command().encode("utf-8")
    ).decode("ascii")
    return render(
        "ru_firewall_rollback_check.sh",
        installer_base64=installer,
        rules_file="/etc/fck-rkn/ru-firewall.nft",
        service_name="fck-rkn-ru-firewall.service",
    )


def check_firewall_rollback(runner: RemoteRunner) -> int:
    """Verify a rejected update preserves saved and active RU firewall rules."""
    return execute(
        runner,
        build_firewall_rollback_check_command(),
        "Checking RU firewall update rollback",
        FIREWALL_ROLLBACK_TIMEOUT_SECONDS,
        stream_output=True,
    )


def build_crawler_source_check_command() -> str:
    return render("ru_firewall_crawler_source_check.sh")


def check_firewall_crawler_source(
    runner: RemoteRunner, ru_server_address: str, crawler_source_address: str
) -> int:
    """Verify crawler-range traffic is logged and allowed during observation."""
    try:
        ru_address = ipaddress.ip_address(ru_server_address)
        crawler_address = ipaddress.ip_address(crawler_source_address)
    except (TypeError, ValueError) as error:
        logger.error("RU and crawler test addresses must be valid IP addresses")
        return 1
    if ru_address.version != 4 or crawler_address.version != 4:
        logger.error("The isolated crawler-source check requires IPv4 addresses")
        return 1
    return run_remote_with_input(
        runner,
        build_crawler_source_check_command(),
        json.dumps(
            {
                "ru_server_address": ru_address.compressed,
                "crawler_source_address": crawler_address.compressed,
            }
        ),
        "Checking RU firewall rejection of an isolated crawler-range source",
        timeout=60,
    )


def check_firewall_crawler_source_from_config(
    runner: RemoteRunner, config_json: str
) -> int:
    try:
        config = json.loads(config_json)
        if not isinstance(config, dict):
            raise ValueError("crawler-source test configuration must be an object")
        ru_address = config["ru_server_address"]
        crawler_address = config["crawler_source_address"]
    except (KeyError, TypeError, ValueError) as error:
        logger.error("Invalid crawler-source test configuration: %s", error)
        return 1
    return check_firewall_crawler_source(runner, ru_address, crawler_address)


def check_firewall_policy(runner: RemoteRunner, config_json: str) -> int:
    """Check both permitted Outline forwards and the RU firewall egress policy."""
    for description, check in (
        ("Squid forward", check_squid_forward),
        ("MTProxy forward", check_mtproxy_forward),
    ):
        if check(runner) != 0:
            logger.error("RU firewall allowed %s path check failed", description)
            return 1
    return check_firewall_egress(runner, config_json)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server service checks.")
    parser.add_argument(
        "action",
        choices=(
            "outline-client",
            "outline-health",
            "squid-forward",
            "mtproxy-forward",
            "mtproxy-forward-blocked",
            "tunnel-blocked",
            "firewall-policy",
            "firewall-rollback",
            "firewall-crawler-source",
        ),
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
    if args.action == "mtproxy-forward":
        return check_mtproxy_forward(runner)
    if args.action == "outline-health":
        return check_outline_health(runner)
    if args.action == "mtproxy-forward-blocked":
        return check_mtproxy_forward_blocked(runner)
    if args.action == "tunnel-blocked":
        return check_tunnel_blocked(runner)
    if args.action == "firewall-rollback":
        return check_firewall_rollback(runner)
    if args.action == "firewall-crawler-source":
        return check_firewall_crawler_source_from_config(runner, sys.stdin.read())
    if args.action == "firewall-policy":
        return check_firewall_policy(runner, sys.stdin.read())

    # The key is read from stdin so it never appears in argv or shell history.
    access_url = sys.stdin.readline()
    return check_outline_client(runner, access_url)


if __name__ == "__main__":
    raise SystemExit(main())
