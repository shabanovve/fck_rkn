"""Command-line entry point for the fck-rkn-test environment orchestrator."""

from __future__ import annotations

import ipaddress
import json
import logging
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import compute_v1
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn import last_run
from fck_rkn.agents.eu_server import (
    apply_eu_firewall,
    create_outline_user,
    deploy_mtproxy,
    deploy_outline,
    deploy_squid,
    run_youtube_ping,
)
from fck_rkn.agents.eu_server_test import (
    check_eu_firewall,
    check_eu_firewall_rollback,
    check_mtproxy,
    check_outline_health,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.agents.outline import load_outline_image
from fck_rkn.agents.ru_server import (
    apply_ru_firewall,
    deploy_outline_client,
    deploy_mtproxy_forward,
    deploy_outline_server,
    deploy_squid_forward,
    recover_mtproxy_forward,
    recover_tunnel,
    stop_mtproxy_forward,
    stop_tunnel,
    parse_access_url,
)
from fck_rkn.agents.test_client import (
    check_mtproxy_tunnel,
    check_outline_connection,
    check_squid_tunnel,
    connect_to_outline,
    deploy_mtproxy_tunnel,
    deploy_squid_tunnel,
    request_youtube,
)
from fck_rkn.agents.ru_server_test import (
    check_firewall_crawler_source,
    check_firewall_policy,
    check_firewall_vpn_client,
    check_firewall_rollback,
    check_outline_client,
    check_outline_health as check_ru_outline_health,
    check_mtproxy_forward,
    check_mtproxy_forward_blocked,
    check_squid_forward,
    check_tunnel_blocked,
)
from fck_rkn.orchestration.configure import (
    TEMPLATE_FILE,
    configure_main,
    configure_test_env,
)
from fck_rkn.config import (
    CONFIG_FILE,
    DotenvSyntaxError,
    TestEnvironmentSettings,
    load_config,
)
from fck_rkn.gcp.compute import (
    INSTANCE_NAME,
    RU_INSTANCE_NAME,
    GcpInspector,
    ensure_eu_server,
    ensure_ru_server,
    ensure_test_client,
    lookup_test_client,
    private_address_of,
    stop_test_project_vms,
    validate_project_access,
)
from fck_rkn.gcp.runner import GcloudIapRunner
from fck_rkn.orchestration.outline_image import (
    build_outline_image,
    export_outline_image,
)


logger = logging.getLogger(__name__)
RU_PROBE_OUTLINE_USER = "ru-server-feasibility-probe"
TEST_CLIENT_OUTLINE_USER = "test-client"
RKN_BLACKLIST_URL = (
    "https://raw.githubusercontent.com/C24Be/AS_Network_List/main/"
    "blacklists_nftables/blacklist.nft"
)
RKN_BLACKLIST_TIMEOUT_SECONDS = 30
OTHER_HOST_TEST_ADDRESS = "1.1.1.1"


def _blacklist_networks(content: str, set_name: str, version: int) -> list[str]:
    """Extract and validate one non-empty address set from the upstream nft file."""
    marker = f"set {set_name}"
    set_start = content.find(marker)
    if set_start < 0:
        raise ValueError(f"RKN blacklist is missing {set_name}")
    elements_start = content.find("elements", set_start)
    if elements_start < 0:
        raise ValueError(f"RKN blacklist set {set_name} has no elements")
    opening_brace = content.find("{", elements_start)
    closing_brace = content.find("}", opening_brace + 1)
    if opening_brace < 0 or closing_brace < 0:
        raise ValueError(f"RKN blacklist set {set_name} is malformed")
    values: set[str] = set()
    for raw_value in content[opening_brace + 1 : closing_brace].split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as error:
            raise ValueError(
                f"RKN blacklist set {set_name} contains an invalid network"
            ) from error
        if network.version != version:
            raise ValueError(
                f"RKN blacklist set {set_name} contains the wrong IP version"
            )
        values.add(network.with_prefixlen)
    if not values:
        raise ValueError(f"RKN blacklist set {set_name} is empty")
    return sorted(
        values,
        key=lambda value: (
            int(ipaddress.ip_network(value).network_address),
            ipaddress.ip_network(value).prefixlen,
        ),
    )


def fetch_rkn_blacklist() -> tuple[list[str], list[str]]:
    """Fetch the confirmed mixed IPv4/IPv6 crawler blacklist artifact."""
    request = urllib.request.Request(
        RKN_BLACKLIST_URL,
        headers={"User-Agent": "fck-rkn-test-firewall"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=RKN_BLACKLIST_TIMEOUT_SECONDS
        ) as response:
            content = response.read().decode("utf-8")
    except (OSError, UnicodeError, urllib.error.URLError) as error:
        raise RuntimeError("Could not fetch the RKN crawler blacklist") from error
    ipv4 = _blacklist_networks(content, "blacklist_v4", 4)
    ipv6 = _blacklist_networks(content, "blacklist_v6", 6)
    return ipv4, ipv6


def _crawler_probe_address(networks: list[str]) -> str:
    if not networks:
        raise ValueError("The RKN IPv4 crawler ranges are empty")
    try:
        network = ipaddress.ip_network(networks[0], strict=True)
    except ValueError as error:
        raise ValueError("The first RKN IPv4 crawler range is invalid") from error
    if network.version != 4:
        raise ValueError("The crawler-source probe requires an IPv4 range")
    return str(network.network_address)


# On GCP the metadata server also provides DNS, DHCP and time service.
GCP_VM_SERVICES_ADDRESS = "169.254.169.254"


def _gcp_vm_services(subnet_cidr: str) -> dict[str, object]:
    return {
        "metadata_address": GCP_VM_SERVICES_ADDRESS,
        "dns_addresses": [GCP_VM_SERVICES_ADDRESS],
        "dhcp_server_address": GCP_VM_SERVICES_ADDRESS,
        "ntp_addresses": [GCP_VM_SERVICES_ADDRESS],
        "local_networks": [subnet_cidr],
    }


def build_eu_firewall_configuration(
    *,
    ru_server_address: str,
    rkn_ipv4: list[str],
    rkn_ipv6: list[str],
    subnet_cidr: str,
) -> str:
    """Build the non-secret runtime input accepted by the EU firewall agent."""
    return json.dumps(
        {
            "ru_server_address": ipaddress.ip_address(ru_server_address).compressed,
            "rkn_ipv4": rkn_ipv4,
            "rkn_ipv6": rkn_ipv6,
            "vm_services": _gcp_vm_services(subnet_cidr),
        }
    )


def build_ru_firewall_configuration(
    *,
    eu_server_address: str,
    eu_access_url: str,
    other_host_address: str,
    rkn_ipv4: list[str],
    rkn_ipv6: list[str],
    subnet_cidr: str,
) -> str:
    """Build the non-secret runtime input accepted by the RU firewall agent."""
    try:
        _, _, outline_address, outline_port = parse_access_url(eu_access_url)
    except ValueError as error:
        raise ValueError("EU Outline access URL is invalid") from error
    return json.dumps(
        {
            "eu_server_address": ipaddress.ip_address(eu_server_address).compressed,
            "eu_outline_address": ipaddress.ip_address(outline_address).compressed,
            "eu_outline_port": outline_port,
            "other_host_address": ipaddress.ip_address(
                other_host_address
            ).compressed,
            "rkn_ipv4": rkn_ipv4,
            "rkn_ipv6": rkn_ipv6,
            "vm_services": _gcp_vm_services(subnet_cidr),
        }
    )


def _shutdown_vms(settings: TestEnvironmentSettings) -> int:
    try:
        stopped = stop_test_project_vms(settings)
    except GoogleAuthError:
        logger.error("GCP authentication failed during VM shutdown")
        print("GCP authentication failed during VM shutdown.", file=sys.stderr)
        return 3
    except GoogleAPICallError as error:
        logger.error("GCP VM shutdown failed")
        print("GCP VM shutdown failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 14
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.error("VM shutdown failed: %s", error)
        print(f"VM shutdown failed: {error}", file=sys.stderr)
        return 14
    print(f"Stopped {len(stopped)} running VM(s) in {settings.GCP_PROJECT_ID}.")
    return 0


def _provision_server(
    name: str,
    ensure_server: Callable[[TestEnvironmentSettings], compute_v1.Instance],
    settings: TestEnvironmentSettings,
    failure_code: int,
    auth_code: int,
) -> tuple[compute_v1.Instance | None, int]:
    """Provision a VM; on failure report it and return (None, exit code)."""
    try:
        return ensure_server(settings), 0
    except GoogleAPICallError as error:
        logger.error("GCP %s provisioning failed", name)
        print(f"GCP {name} provisioning failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return None, failure_code
    except GoogleAuthError:
        logger.error("GCP authentication failed during %s provisioning", name)
        print(
            f"GCP authentication failed during {name} provisioning. "
            "Verify the active gcloud account and ADC credentials.",
            file=sys.stderr,
        )
        return None, auth_code
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.error("%s provisioning failed: %s", name, error)
        print(f"{name} provisioning failed: {error}", file=sys.stderr)
        return None, failure_code


def main() -> int:
    if sys.argv[1:2] == ["configure"]:
        return configure_main()
    with tempfile.TemporaryDirectory() as directory:
        return _run(Path(directory))


def _configure_missing_test_env() -> int:
    if CONFIG_FILE.exists():
        return 0
    try:
        report = configure_test_env(CONFIG_FILE, TEMPLATE_FILE, GcpInspector())
    except GoogleAuthError:
        print(
            "Could not automatically create config/test.env because GCP "
            "authentication failed. Configure Application Default Credentials "
            "or run `.venv/bin/test-env-host configure`.",
            file=sys.stderr,
        )
        return 3
    except GoogleAPICallError as error:
        print(
            "Could not automatically create config/test.env because a GCP "
            "request failed. Run `.venv/bin/test-env-host configure` to retry.",
            file=sys.stderr,
        )
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        return 14
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Could not automatically create config/test.env. "
            "Run `.venv/bin/test-env-host configure` to diagnose and retry.",
            file=sys.stderr,
        )
        print(f"  {error}", file=sys.stderr)
        return 2
    for line in report:
        print(line)
    return 0


def _run(image_directory: Path) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logger.info("Starting test environment orchestrator")
    config_status = _configure_missing_test_env()
    if config_status:
        return config_status
    try:
        settings: TestEnvironmentSettings = load_config()
    except DotenvSyntaxError as error:
        logger.error("Test configuration has invalid dotenv syntax")
        print("Configuration file syntax error.", file=sys.stderr)
        print(f"  File: {error.path}", file=sys.stderr)
        print(f"  Line: {error.line}", file=sys.stderr)
        print(
            "  Check that each non-comment line uses KEY=value syntax. "
            "The line contents are intentionally not shown.",
            file=sys.stderr,
        )
        return 2
    except OSError as error:
        logger.error("Could not read the test configuration file")
        print("Could not read the test configuration file.", file=sys.stderr)
        print(f"  File: {error.filename or 'config/test.env'}", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        return 2
    except (SettingsError, ValidationError) as error:
        logger.error("Test configuration is missing or invalid")
        print(
            "Configuration error: test settings are missing or invalid.",
            file=sys.stderr,
        )
        if isinstance(error, ValidationError):
            details = error.errors(include_input=False)
            missing = sorted(
                {
                    str(detail["loc"][0])
                    for detail in details
                    if detail["type"] == "missing"
                }
            )
            if missing:
                print(
                    f"  Missing required setting(s): {', '.join(missing)}.",
                    file=sys.stderr,
                )
            else:
                print(details, file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 2

    try:
        validate_project_access(settings)
    except GoogleAuthError:
        logger.error("GCP authentication failed during project access check")
        print(
            "GCP authentication failed. Configure Application Default "
            "Credentials with `gcloud auth application-default login`.",
            file=sys.stderr,
        )
        return 3
    except GoogleAPICallError as error:
        logger.error("GCP project access check failed")
        print("GCP project access check failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 4
    except ValueError as error:
        logger.error("GCP project validation failed")
        print(f"GCP project validation failed: {error}", file=sys.stderr)
        return 4

    if sys.argv[1:2] == ["shutdown-vms"]:
        return _shutdown_vms(settings)

    instance, exit_code = _provision_server(
        "eu-server", ensure_eu_server, settings, failure_code=5, auth_code=3
    )
    if instance is None:
        return exit_code

    try:
        eu_private_address = private_address_of(instance)
    except ValueError as error:
        logger.error("%s", error)
        print(str(error), file=sys.stderr)
        return 5

    eu_runner = GcloudIapRunner(
        INSTANCE_NAME, settings.GCP_PROJECT_ID, settings.GCP_ZONE
    )

    print(
        f"GCP access is valid; {instance.name} is running and SSH-ready "
        f"in {settings.GCP_ZONE}."
    )
    logger.info("Invoking eu-server-agent ping check")
    ping_exit_code = run_youtube_ping(eu_runner)
    if ping_exit_code != 0:
        logger.error(
            "eu-server-agent ping check failed with exit status %d",
            ping_exit_code,
        )
        print(
            f"eu-server-agent ping check failed with exit status "
            f"{ping_exit_code}.",
            file=sys.stderr,
        )
        return 6

    logger.info("eu-server-agent ping check passed")
    logger.info("Invoking eu-server-agent Squid deployment")
    deploy_exit_code = deploy_squid(eu_runner)
    if deploy_exit_code != 0:
        logger.error(
            "eu-server-agent Squid deployment failed with exit status %d",
            deploy_exit_code,
        )
        print(
            "eu-server-agent Squid deployment failed with exit status "
            f"{deploy_exit_code}.",
            file=sys.stderr,
        )
        return 7

    logger.info("eu-server-agent Squid deployment passed")
    logger.info("Invoking eu-server-test-agent Squid startup check")
    squid_test_exit_code = check_squid_startup(eu_runner)
    if squid_test_exit_code != 0:
        logger.error(
            "eu-server-test-agent Squid startup check failed with exit "
            "status %d",
            squid_test_exit_code,
        )
        print(
            "eu-server-test-agent Squid startup check failed with exit "
            f"status {squid_test_exit_code}.",
            file=sys.stderr,
        )
        return 8

    logger.info("eu-server-test-agent Squid startup check passed")
    logger.info("Invoking eu-server-test-agent Squid HTTP check")
    squid_http_exit_code = check_squid_http(eu_runner)
    if squid_http_exit_code != 0:
        logger.error(
            "eu-server-test-agent Squid HTTP check failed with exit status %d",
            squid_http_exit_code,
        )
        print(
            "eu-server-test-agent Squid HTTP check failed with exit status "
            f"{squid_http_exit_code}.",
            file=sys.stderr,
        )
        return 9

    logger.info("eu-server-test-agent Squid HTTP check passed")
    logger.info("Building the Outline Server image once on this host")
    if build_outline_image() != 0:
        print("Could not build the Outline Server image.", file=sys.stderr)
        return 26
    image_archive = export_outline_image(image_directory)
    if image_archive is None:
        print("Could not export the Outline Server image.", file=sys.stderr)
        return 26
    if load_outline_image(eu_runner, image_archive) != 0:
        print(
            "Could not load the Outline Server image on eu-server.",
            file=sys.stderr,
        )
        return 27

    logger.info("Invoking eu-server-agent Outline deployment")
    outline_deploy_exit_code = deploy_outline(eu_runner, eu_private_address)
    if outline_deploy_exit_code != 0:
        logger.error(
            "eu-server-agent Outline deployment failed with exit status %d",
            outline_deploy_exit_code,
        )
        print(
            "eu-server-agent Outline deployment failed with exit status "
            f"{outline_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 10

    logger.info("eu-server-agent Outline deployment passed")
    logger.info("Invoking eu-server-test-agent Outline health check")
    outline_health_exit_code = check_outline_health(eu_runner)
    if outline_health_exit_code != 0:
        logger.error(
            "eu-server-test-agent Outline health check failed with exit "
            "status %d",
            outline_health_exit_code,
        )
        print(
            "eu-server-test-agent Outline health check failed with exit "
            f"status {outline_health_exit_code}.",
            file=sys.stderr,
        )
        return 11

    logger.info("eu-server-test-agent Outline health check passed")

    mtproxy_steps = (
        ("MTProxy deployment", "eu-server-agent", deploy_mtproxy, 36),
        ("MTProxy check", "eu-server-test-agent", check_mtproxy, 37),
    )
    for description, agent, step, failure_code in mtproxy_steps:
        logger.info("Invoking %s %s", agent, description)
        step_exit_code = step(eu_runner)
        if step_exit_code != 0:
            logger.error(
                "%s %s failed with exit status %d",
                agent,
                description,
                step_exit_code,
            )
            print(
                f"{agent} {description} failed with exit status "
                f"{step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("eu-server-test-agent MTProxy check passed")

    ru_instance, exit_code = _provision_server(
        "ru-server", ensure_ru_server, settings, failure_code=12, auth_code=12
    )
    if ru_instance is None:
        return exit_code

    ru_runner = GcloudIapRunner(
        RU_INSTANCE_NAME, settings.GCP_PROJECT_ID, settings.GCP_ZONE
    )

    try:
        ru_private_address = private_address_of(ru_instance)
    except ValueError as error:
        logger.error("%s", error)
        print(str(error), file=sys.stderr)
        return 12

    if load_outline_image(ru_runner, image_archive) != 0:
        print(
            "Could not load the Outline Server image on ru-server.",
            file=sys.stderr,
        )
        return 28

    logger.info("Invoking ru-server-agent Outline Server deployment")
    ru_outline_deploy_exit_code = deploy_outline_server(
        ru_runner, ru_private_address
    )
    if ru_outline_deploy_exit_code != 0:
        logger.error(
            "ru-server-agent Outline Server deployment failed with exit "
            "status %d",
            ru_outline_deploy_exit_code,
        )
        print(
            "ru-server-agent Outline Server deployment failed with exit "
            f"status {ru_outline_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 24

    logger.info("Invoking ru-server-test-agent Outline health check")
    ru_outline_health_exit_code = check_ru_outline_health(ru_runner)
    if ru_outline_health_exit_code != 0:
        logger.error(
            "ru-server-test-agent Outline health check failed with exit "
            "status %d",
            ru_outline_health_exit_code,
        )
        print(
            "ru-server-test-agent Outline health check failed with exit "
            f"status {ru_outline_health_exit_code}.",
            file=sys.stderr,
        )
        return 25

    logger.info("ru-server-test-agent Outline health check passed")
    logger.info("Creating the Outline access key for the RU feasibility probe")
    access_url = create_outline_user(eu_runner, RU_PROBE_OUTLINE_USER)
    if access_url is None:
        logger.error("Could not create the Outline access key for ru-server")
        print("Could not create the Outline access key for ru-server.", file=sys.stderr)
        return 13

    logger.info("Invoking ru-server-agent Outline client deployment")
    client_deploy_exit_code = deploy_outline_client(ru_runner, access_url)
    if client_deploy_exit_code != 0:
        logger.error(
            "ru-server-agent Outline client deployment failed with exit "
            "status %d",
            client_deploy_exit_code,
        )
        print(
            "ru-server-agent Outline client deployment failed with exit "
            f"status {client_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 16

    logger.info("Invoking ru-server-test-agent Outline client check")
    client_check_exit_code = check_outline_client(ru_runner, access_url)
    if client_check_exit_code != 0:
        logger.error(
            "ru-server-test-agent Outline client check failed with exit "
            "status %d",
            client_check_exit_code,
        )
        print(
            "ru-server-test-agent Outline client check failed with exit "
            f"status {client_check_exit_code}.",
            file=sys.stderr,
        )
        return 17

    logger.info("RU Outline client verified")

    logger.info("Invoking ru-server-agent EU Squid forward deployment")
    forward_deploy_exit_code = deploy_squid_forward(ru_runner, access_url)
    if forward_deploy_exit_code != 0:
        logger.error(
            "ru-server-agent Squid forward deployment failed with exit "
            "status %d",
            forward_deploy_exit_code,
        )
        print(
            "ru-server-agent Squid forward deployment failed with exit "
            f"status {forward_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 18

    logger.info("Invoking ru-server-test-agent Squid forward check")
    forward_check_exit_code = check_squid_forward(ru_runner)
    if forward_check_exit_code != 0:
        logger.error(
            "ru-server-test-agent Squid forward check failed with exit "
            "status %d",
            forward_check_exit_code,
        )
        print(
            "ru-server-test-agent Squid forward check failed with exit "
            f"status {forward_check_exit_code}.",
            file=sys.stderr,
        )
        return 19

    logger.info("RU forward to EU Squid verified")

    mtproxy_deploy_steps = (
        ("MTProxy forward deployment", deploy_mtproxy_forward, 38, True),
        ("MTProxy forward check", check_mtproxy_forward, 39, False),
    )
    for description, step, failure_code, needs_key in mtproxy_deploy_steps:
        logger.info("Running RU %s", description)
        step_exit_code = (
            step(ru_runner, access_url) if needs_key else step(ru_runner)
        )
        if step_exit_code != 0:
            logger.error(
                "RU %s failed with exit status %d", description, step_exit_code
            )
            print(
                f"RU {description} failed with exit status {step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("RU forward to EU MTProxy verified")

    tunnel_loss_steps = (
        ("tunnel loss simulation", stop_tunnel, 20),
        ("blocked-traffic check", check_tunnel_blocked, 21),
        ("tunnel recovery", recover_tunnel, 22),
        ("post-recovery forward check", check_squid_forward, 23),
        ("MTProxy tunnel loss simulation", stop_mtproxy_forward, 40),
        (
            "MTProxy blocked-traffic check",
            check_mtproxy_forward_blocked,
            41,
        ),
        ("MTProxy forward recovery", recover_mtproxy_forward, 42),
        ("post-recovery MTProxy forward check", check_mtproxy_forward, 43),
    )
    for description, step, failure_code in tunnel_loss_steps:
        logger.info("Running RU %s", description)
        step_exit_code = step(ru_runner)
        if step_exit_code != 0:
            logger.error(
                "RU %s failed with exit status %d", description, step_exit_code
            )
            print(
                f"RU {description} failed with exit status {step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("RU tunnel-loss blocking and recovery verified")

    try:
        existing_client = lookup_test_client(settings)
        logger.info(
            "test-client %s",
            "exists" if existing_client is not None else "does not exist; creating it",
        )
        test_client = ensure_test_client(settings)
    except Exception as error:
        logger.error("Preparing test-client failed: %s", error)
        print(f"Preparing test-client failed: {error}", file=sys.stderr)
        return 29
    logger.info("test-client %s is running and SSH-ready", test_client.name)

    logger.info("Creating the RU Outline access key for test-client")
    client_access_url = create_outline_user(ru_runner, TEST_CLIENT_OUTLINE_USER)
    if client_access_url is None:
        logger.error("Could not create the RU Outline access key for test-client")
        print(
            "Could not create the RU Outline access key for test-client.",
            file=sys.stderr,
        )
        return 30

    client_runner = GcloudIapRunner(
        test_client.name, settings.GCP_PROJECT_ID, settings.GCP_ZONE
    )
    client_steps = (
        ("connection", connect_to_outline, 31),
        ("connection check", check_outline_connection, 32),
    )
    for description, step, failure_code in client_steps:
        logger.info("Running test-client Outline %s", description)
        step_exit_code = step(client_runner, client_access_url)
        if step_exit_code != 0:
            logger.error(
                "test-client Outline %s failed with exit status %d",
                description,
                step_exit_code,
            )
            print(
                f"test-client Outline {description} failed with exit status "
                f"{step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("test-client is connected to RU Outline")

    squid_tunnel_steps = (
        (
            "Squid tunnel deployment",
            lambda: deploy_squid_tunnel(client_runner, client_access_url),
            33,
        ),
        ("Squid tunnel check", lambda: check_squid_tunnel(client_runner), 34),
    )
    for description, step, failure_code in squid_tunnel_steps:
        logger.info("Running test-client %s", description)
        step_exit_code = step()
        if step_exit_code != 0:
            logger.error(
                "test-client %s failed with exit status %d",
                description,
                step_exit_code,
            )
            print(
                f"test-client {description} failed with exit status "
                f"{step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    mtproxy_tunnel_steps = (
        (
            "MTProxy tunnel deployment",
            lambda: deploy_mtproxy_tunnel(client_runner, client_access_url),
            44,
        ),
        (
            "MTProxy tunnel check",
            lambda: check_mtproxy_tunnel(client_runner),
            45,
        ),
    )
    for description, step, failure_code in mtproxy_tunnel_steps:
        logger.info("Running test-client %s", description)
        step_exit_code = step()
        if step_exit_code != 0:
            logger.error(
                "test-client %s failed with exit status %d",
                description,
                step_exit_code,
            )
            print(
                f"test-client {description} failed with exit status "
                f"{step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code

    try:
        rkn_ipv4, rkn_ipv6 = fetch_rkn_blacklist()
        crawler_probe_address = _crawler_probe_address(rkn_ipv4)
        firewall_config = build_ru_firewall_configuration(
            eu_server_address=eu_private_address,
            eu_access_url=access_url,
            other_host_address=OTHER_HOST_TEST_ADDRESS,
            rkn_ipv4=rkn_ipv4,
            rkn_ipv6=rkn_ipv6,
            subnet_cidr=settings.GCP_SUBNET_CIDR,
        )
        eu_firewall_config = build_eu_firewall_configuration(
            ru_server_address=ru_private_address,
            rkn_ipv4=rkn_ipv4,
            rkn_ipv6=rkn_ipv6,
            subnet_cidr=settings.GCP_SUBNET_CIDR,
        )
    except (OSError, RuntimeError, ValueError) as error:
        logger.error("Preparing RU firewall inputs failed: %s", error)
        print(f"Preparing RU firewall inputs failed: {error}", file=sys.stderr)
        return 46

    last_run.save(last_run.EU_FIREWALL_CONFIG, eu_firewall_config)
    last_run.save(last_run.RU_FIREWALL_CONFIG, firewall_config)
    last_run.save(last_run.RU_PROBE_KEY, access_url)
    last_run.save(last_run.TEST_CLIENT_KEY, client_access_url)
    last_run.save(
        last_run.CRAWLER_SOURCE_CONFIG,
        json.dumps(
            {
                "ru_server_address": ru_private_address,
                "crawler_source_address": crawler_probe_address,
            }
        ),
    )

    logger.info("Installing the plan-item-10 firewall on eu-server")
    eu_firewall_exit_code = apply_eu_firewall(eu_runner, eu_firewall_config)
    if eu_firewall_exit_code != 0:
        logger.error(
            "eu-server-agent firewall installation failed with exit status %d",
            eu_firewall_exit_code,
        )
        print(
            "eu-server-agent firewall installation failed with exit status "
            f"{eu_firewall_exit_code}.",
            file=sys.stderr,
        )
        return 51
    eu_firewall_checks = (
        (
            "EU firewall policy",
            lambda: check_eu_firewall(eu_runner, eu_firewall_config),
            52,
        ),
        ("EU firewall rollback", lambda: check_eu_firewall_rollback(eu_runner), 53),
    )
    for description, step, failure_code in eu_firewall_checks:
        logger.info("Running %s check", description)
        step_exit_code = step()
        if step_exit_code != 0:
            logger.error(
                "%s check failed with exit status %d", description, step_exit_code
            )
            print(
                f"{description} check failed with exit status {step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("EU firewall policy and rollback checks passed")

    logger.info("Installing the plan-item-10 firewall on ru-server")
    firewall_apply_exit_code = apply_ru_firewall(ru_runner, firewall_config)
    if firewall_apply_exit_code != 0:
        logger.error(
            "ru-server-agent firewall installation failed with exit status %d",
            firewall_apply_exit_code,
        )
        print(
            "ru-server-agent firewall installation failed with exit status "
            f"{firewall_apply_exit_code}.",
            file=sys.stderr,
        )
        return 47

    firewall_checks = (
        (
            "RU firewall policy",
            lambda: check_firewall_policy(ru_runner, firewall_config),
            48,
        ),
        ("RU firewall rollback", lambda: check_firewall_rollback(ru_runner), 49),
        (
            "isolated crawler-source denial",
            lambda: check_firewall_crawler_source(
                ru_runner,
                ru_private_address,
                crawler_probe_address,
            ),
            50,
        ),
        (
            "VPN client paths through the RU firewall",
            lambda: check_firewall_vpn_client(
                ru_runner, client_access_url, firewall_config
            ),
            54,
        ),
    )
    for description, step, failure_code in firewall_checks:
        logger.info("Running %s check", description)
        step_exit_code = step()
        if step_exit_code != 0:
            logger.error(
                "%s check failed with exit status %d", description, step_exit_code
            )
            print(
                f"{description} check failed with exit status {step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("RU firewall policy, rollback, crawler-source, and VPN client checks passed")

    # Rejected updates must leave the previous rules serving traffic, so repeat
    # the whole matrix now that both firewalls are active and rollbacks are done.
    final_checks = (
        (
            "final EU firewall policy",
            lambda: check_eu_firewall(eu_runner, eu_firewall_config),
            55,
        ),
        (
            "final RU firewall policy",
            lambda: check_firewall_policy(ru_runner, firewall_config),
            56,
        ),
        (
            "final VPN client paths",
            lambda: check_firewall_vpn_client(
                ru_runner, client_access_url, firewall_config
            ),
            57,
        ),
    )
    for description, step, failure_code in final_checks:
        logger.info("Running %s check after the update rollback checks", description)
        step_exit_code = step()
        if step_exit_code != 0:
            logger.error(
                "%s check failed with exit status %d", description, step_exit_code
            )
            print(
                f"{description} check failed with exit status {step_exit_code}.",
                file=sys.stderr,
            )
            return failure_code
    logger.info("Plan item 10 firewall matrix passed on both servers")

    logger.info("Running test-client youtube.com request through the proxy path")
    client_request_exit_code = request_youtube(client_runner)
    if client_request_exit_code != 0:
        logger.error(
            "test-client youtube.com request failed with exit status %d",
            client_request_exit_code,
        )
        print(
            "test-client youtube.com request failed with exit status "
            f"{client_request_exit_code}.",
            file=sys.stderr,
        )
        return 35
    logger.info("test-client reached youtube.com through RU and EU Outline")

    print(
        f"{ru_instance.name} is available in project "
        f"{settings.GCP_PROJECT_ID}, zone {settings.GCP_ZONE}."
    )
    logger.info("Test environment preflight and provisioning completed")
    return 0
