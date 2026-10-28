"""Checks executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
from fck_rkn.agents.firewall_common import (
    firewall_absent_check_command,
    firewall_rollback_check_command,
    load_config,
    parse_minimal_vm_services,
)
from fck_rkn.agents.firewall_test_common import check_rkn_blacklist_updater
from fck_rkn.agents.outline import (
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_API_PORT,
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
    build_client_config,
    parse_access_url,
    run_remote_with_input,
)
from fck_rkn import last_run
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


VPN_CHECK_LOCAL_PORT = 14080
SNAPD_CHECK_HOST = "api.snapcraft.io"
FIREWALL_CHECK_TIMEOUT_SECONDS = 240
VPN_CLIENT_CHECK_TIMEOUT_SECONDS = 240


def build_firewall_check_command() -> str:
    """Create a remote check for RU's active default-deny firewall policy."""
    return render(
        "ru_firewall_check.sh",
        vpn_port=OUTLINE_ACCESS_KEY_PORT,
        api_port=OUTLINE_API_PORT,
        proxy_ports=f"{FORWARD_LOCAL_PORT} {MTPROXY_FORWARD_LOCAL_PORT}",
    )


def _first_ipv4(addresses: list[str], name: str) -> str:
    for address in addresses:
        if ipaddress.ip_address(address).version == 4:
            return address
    raise ValueError(f"{name} must contain an IPv4 address")


def _load_firewall_test_config(config_json: str) -> dict:
    config = load_config(config_json)
    for key in ("eu_server_address", "eu_outline_address", "other_host_address"):
        config[key] = _validated_address(config.get(key), key)
    if config["other_host_address"] in {
        config["eu_server_address"],
        config["eu_outline_address"],
    }:
        raise ValueError("other_host_address must be a distinct host")
    return config


def _server_to_github_check_hosts(config: dict) -> list[str]:
    hosts = config.get("server_to_github_check_hosts")
    if not isinstance(hosts, list) or not hosts or not all(
        isinstance(host, str) and host.strip() for host in hosts
    ):
        raise ValueError("server_to_github_check_hosts must be a non-empty list of host names")
    return hosts


def _service_sets(config: dict) -> list[str]:
    """Name the address sets the generator creates for the supplied ranges."""
    names = []
    for key, prefix in (("server_to_github_addresses", "server_to_github"), ("snapd_networks", "snapd")):
        versions = {ipaddress.ip_network(item).version for item in config[key]}
        names.extend(f"{prefix}_ipv{version}" for version in sorted(versions))
    return names


def build_firewall_check_input(config_json: str) -> str:
    """Derive the non-secret input of the firewall check from the firewall config."""
    config = _load_firewall_test_config(config_json)
    port = config.get("eu_outline_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("eu_outline_port must be between 1 and 65535")
    services = parse_minimal_vm_services(config.get("vm_services"))
    return json.dumps(
        {
            "eu_server_address": config["eu_server_address"],
            "eu_outline_address": config["eu_outline_address"],
            "eu_outline_port": port,
            "other_host_address": config["other_host_address"],
            "ssh_port": config.get("ssh_port", 22),
            "dhcp_address": services.dhcp_server_address.compressed,
            "vm_service_address": _first_ipv4(
                [services.metadata_address.compressed], "vm_service_address"
            ),
            "service_sets": _service_sets(config),
            "service_hosts": [*_server_to_github_check_hosts(config), SNAPD_CHECK_HOST],
            "ntp_address": _first_ipv4(
                [address.compressed for address in services.ntp_addresses],
                "ntp_addresses",
            ),
        }
    )



def check_ru_firewall_readonly(runner: RemoteRunner, config_json: str) -> int:
    try:
        config = load_config(config_json)
        for key in ("eu_server_address", "eu_outline_address"):
            config[key] = _validated_address(config.get(key), key)
        port = config.get("eu_outline_port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("eu_outline_port must be between 1 and 65535")
    except (TypeError, ValueError, KeyError) as error:
        logger.error("Invalid RU firewall test configuration: %s", error)
        return 1
    logger.info("Verifying the active RU firewall policy on ru-server (read-only)")
    return execute(
        runner,
        render("ru_firewall_readonly_check.sh", vpn_port=OUTLINE_ACCESS_KEY_PORT, api_port=OUTLINE_API_PORT),
        "RU firewall read-only verification",
        timeout=10,
        stdin=json.dumps(
            {
                "eu_server_address": config["eu_server_address"],
                "eu_outline_address": config["eu_outline_address"],
                "eu_outline_port": port,
                "ssh_port": config.get("ssh_port", 22),
            }
        ),
        print_output=True,
        print_stderr=True,
    )


def check_firewall_policy_rules(runner: RemoteRunner, config_json: str) -> int:
    """Verify default deny, logging and the allowed paths of the RU firewall."""
    try:
        remote_input = build_firewall_check_input(config_json)
    except (TypeError, ValueError, KeyError) as error:
        logger.error("Invalid RU firewall test configuration: %s", error)
        return 1
    return run_remote_with_input(
        runner,
        build_firewall_check_command(),
        remote_input,
        "Checking the RU firewall's default-deny policy and allowed paths",
        timeout=FIREWALL_CHECK_TIMEOUT_SECONDS,
        print_stderr=True,
    )


def build_vpn_client_check_command() -> str:
    return render(
        "ru_vpn_client_check.sh",
        image=CLIENT_IMAGE,
        local_port=VPN_CHECK_LOCAL_PORT,
        squid_port=FORWARD_LOCAL_PORT,
        mtproxy_port=MTPROXY_FORWARD_LOCAL_PORT,
    )


def check_firewall_vpn_client(
    runner: RemoteRunner, access_url: str, config_json: str
) -> int:
    """Verify what a real VPN client may reach, using a temporary Outline client."""
    try:
        method, password, host, port = parse_access_url(access_url)
        config = _load_firewall_test_config(config_json)
        ssh_port = config.get("ssh_port", 22)
    except (TypeError, ValueError, KeyError) as error:
        logger.error("Invalid RU VPN client test configuration: %s", error)
        return 1
    return run_remote_with_input(
        runner,
        build_vpn_client_check_command(),
        json.dumps(
            {
                "client": json.loads(build_client_config(method, password, host, port)),
                "other_host_address": config["other_host_address"],
                "eu_server_address": config["eu_server_address"],
                "ssh_port": ssh_port,
            }
        ),
        "Checking what a VPN client can reach through the RU firewall",
        timeout=VPN_CLIENT_CHECK_TIMEOUT_SECONDS,
        print_stderr=True,
    )


def build_firewall_rollback_check_command() -> str:
    return firewall_rollback_check_command("ru")


def check_firewall_rollback(runner: RemoteRunner) -> int:
    """Verify a rejected update preserves saved and active RU firewall rules."""
    return execute(
        runner,
        build_firewall_rollback_check_command(),
        "Checking RU firewall update rollback",
        FIREWALL_ROLLBACK_TIMEOUT_SECONDS,
        stream_output=True,
    )


def check_firewall_removed(runner: RemoteRunner) -> int:
    """Verify no project firewall remains on ru-server."""
    return run_remote_with_input(
        runner,
        firewall_absent_check_command("ru"),
        "",
        "Checking that no RU firewall remains",
        print_stderr=True,
    )


def build_crawler_source_check_command() -> str:
    return render("ru_firewall_crawler_source_check.sh")


def check_firewall_crawler_source(
    runner: RemoteRunner, ru_server_address: str, crawler_source_address: str
) -> int:
    """Verify crawler-range traffic is denied and logged."""
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
    """Check both permitted Outline forwards and the RU firewall policy."""
    for description, check in (
        ("Squid forward", check_squid_forward),
        ("MTProxy forward", check_mtproxy_forward),
    ):
        if check(runner) != 0:
            logger.error("RU firewall allowed %s path check failed", description)
            return 1
    return check_firewall_policy_rules(runner, config_json)


def _action_inputs(action: str, use_last_run: bool) -> tuple[str, ...]:
    """Return the key and configuration inputs of an action.

    Keys are read from stdin or the saved run so they never appear in argv or
    shell history.
    """
    if use_last_run:
        saved = {
            "outline-client": (last_run.RU_PROBE_KEY,),
            "firewall-policy": (last_run.RU_FIREWALL_CONFIG,),
            "firewall-crawler-source": (last_run.CRAWLER_SOURCE_CONFIG,),
            "firewall-vpn-client": (
                last_run.TEST_CLIENT_KEY,
                last_run.RU_FIREWALL_CONFIG,
            ),
        }
        return tuple(last_run.load(name) for name in saved.get(action, ()))
    if action in ("outline-client",):
        return (sys.stdin.readline(),)
    if action == "firewall-vpn-client":
        return sys.stdin.readline(), sys.stdin.read()
    if action in ("firewall-policy", "firewall-crawler-source"):
        return (sys.stdin.read(),)
    return ()


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
            "firewall-vpn-client",
            "firewall-blacklist",
        ),
        help="Check to run",
    )
    parser.add_argument(
        "--last-run",
        action="store_true",
        help="Read the check input saved by the latest test-env-host run "
        "instead of stdin",
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

    try:
        stdin_inputs = _action_inputs(args.action, args.last_run)
    except ValueError as error:
        logger.error("%s", error)
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
        return check_firewall_crawler_source_from_config(runner, stdin_inputs[0])
    if args.action == "firewall-policy":
        return check_firewall_policy(runner, stdin_inputs[0])
    if args.action == "firewall-vpn-client":
        return check_firewall_vpn_client(runner, *stdin_inputs)
    if args.action == "firewall-blacklist":
        return check_rkn_blacklist_updater(runner, "ru")
    return check_outline_client(runner, stdin_inputs[0])


if __name__ == "__main__":
    raise SystemExit(main())
