"""Startup checks executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import ipaddress
import json
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
from fck_rkn.agents.firewall_common import (
    firewall_rollback_check_command,
    load_config,
    parse_minimal_vm_services,
)
from fck_rkn.agents.outline import OUTLINE_HEALTH_CHECK_COMMAND
from fck_rkn import last_run
from fck_rkn.agent_config import eu_server_runner, load_agent_settings
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
SSH_COMMAND_TIMEOUT_SECONDS = 90
FIREWALL_CHECK_TIMEOUT_SECONDS = 180
FIREWALL_ROLLBACK_TIMEOUT_SECONDS = 120
OUTBOUND_TEST_ADDRESS = "1.1.1.1"
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


def build_firewall_check_command() -> str:
    return render(
        "eu_firewall_check.sh",
        vpn_port=OUTLINE_ACCESS_KEY_PORT,
        proxy_ports=f"{SQUID_PORT} {MTPROXY_PORT}",
    )


def build_firewall_check_input(config_json: str) -> str:
    """Derive the non-secret input of the firewall check from the firewall config."""
    config = load_config(config_json)
    services = parse_minimal_vm_services(config.get("vm_services"))
    ranges = config.get("rkn_ipv4")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("rkn_ipv4 must contain at least one network")
    crawler = ipaddress.ip_network(ranges[0], strict=True)
    ru_server = ipaddress.ip_address(config.get("ru_server_address"))
    ssh_port = config.get("ssh_port", 22)
    dns = next((a for a in services.dns_addresses if a.version == 4), None)
    if dns is None or services.metadata_address.version != 4:
        raise ValueError("the firewall check requires IPv4 service addresses")
    return json.dumps(
        {
            "ru_server_address": ru_server.compressed,
            "crawler_source_address": str(crawler.network_address),
            "ssh_port": ssh_port,
            "dns_address": dns.compressed,
            "vm_service_address": services.metadata_address.compressed,
            "outbound_address": OUTBOUND_TEST_ADDRESS,
        }
    )


def check_eu_firewall(runner: RemoteRunner, config_json: str) -> int:
    """Check the EU firewall policy from isolated probe sources on eu-server."""
    try:
        check_input = build_firewall_check_input(config_json)
    except (TypeError, ValueError) as error:
        logger.error("Invalid EU firewall test configuration: %s", error)
        return 1
    logger.info("Checking the EU firewall policy on eu-server")
    return execute(
        runner,
        build_firewall_check_command(),
        "EU firewall policy check",
        FIREWALL_CHECK_TIMEOUT_SECONDS,
        stdin=check_input,
        print_output=True,
        print_stderr=True,
    )


def check_eu_firewall_rollback(runner: RemoteRunner) -> int:
    """Verify a rejected update preserves saved and active EU firewall rules."""
    logger.info("Checking EU firewall update rollback on eu-server")
    return execute(
        runner,
        firewall_rollback_check_command("eu"),
        "EU firewall update rollback check",
        FIREWALL_ROLLBACK_TIMEOUT_SECONDS,
        print_output=True,
        print_stderr=True,
    )


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
        choices=(
            "startup",
            "http",
            "outline",
            "mtproxy",
            "firewall",
            "firewall-rollback",
        ),
        default="startup",
        help="Check to run (default: startup)",
    )
    parser.add_argument(
        "--last-run",
        action="store_true",
        help="Read the firewall input saved by the latest test-env-host run "
        "instead of stdin",
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

    if args.action == "firewall":
        if args.last_run:
            try:
                config_json = last_run.load(last_run.EU_FIREWALL_CONFIG)
            except ValueError as error:
                logger.error("%s", error)
                return 2
        else:
            config_json = sys.stdin.read()
        return check_eu_firewall(runner, config_json)
    if args.action == "firewall-rollback":
        return check_eu_firewall_rollback(runner)
    if args.action == "http":
        return check_squid_http(runner)
    if args.action == "mtproxy":
        return check_mtproxy(runner)
    if args.action == "outline":
        return check_outline_health(runner)
    return check_squid_startup(runner)


if __name__ == "__main__":
    raise SystemExit(main())
