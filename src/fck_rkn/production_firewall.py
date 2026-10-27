"""Production EU and RU firewall input, built from validated prod.env settings."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable

from fck_rkn.agents.eu_firewall import build_eu_firewall_rules
from fck_rkn.agents.rkn_blacklist import (
    fetch_blacklist,
    fetch_github_networks,
    fetch_snapd_networks,
)
from fck_rkn.agents.ru_firewall import build_ru_firewall_rules
from fck_rkn.agents.ru_server import parse_access_url
from fck_rkn.production_config import EuProductionSettings


class FirewallInputError(Exception):
    """The firewall input could not be prepared; the message holds no remote data."""


def prepare_eu_firewall_input(
    settings: EuProductionSettings,
    fetch: Callable[[], tuple[list[str], list[str]]] = fetch_blacklist,
) -> str:
    """Return the validated JSON input for ``apply_eu_firewall``."""
    try:
        rkn_ipv4, rkn_ipv6 = fetch()
    except (RuntimeError, ValueError) as error:
        raise FirewallInputError(f"The RKN blacklist is unavailable: {error}") from None
    ru_server = ipaddress.ip_address(settings.RU_SERVER_ADDRESS)
    for text in (*rkn_ipv4, *rkn_ipv6):
        network = ipaddress.ip_network(text, strict=False)
        if network.version == ru_server.version and ru_server in network:
            raise FirewallInputError(
                "RU_SERVER_ADDRESS is inside an RKN crawler range; the RU peer "
                "would be blocked"
            )
    config_json = json.dumps(
        {
            "ru_server_address": ru_server.compressed,
            "ssh_port": settings.SSH_PORT,
            "rkn_ipv4": rkn_ipv4,
            "rkn_ipv6": rkn_ipv6,
            "vm_services": {
                "dns_addresses": settings.EU_DNS_ADDRESSES,
                "dhcp_server_address": settings.EU_DHCP_SERVER_ADDRESS,
                "ntp_addresses": settings.EU_NTP_ADDRESSES,
                "local_networks": settings.EU_LOCAL_NETWORKS,
            },
        }
    )
    try:
        build_eu_firewall_rules(config_json)
    except ValueError as error:
        raise FirewallInputError(f"Invalid EU firewall input: {error}") from None
    return config_json


def prepare_ru_firewall_input(
    settings: EuProductionSettings,
    access_url: str,
    fetch_rkn: Callable[[], tuple[list[str], list[str]]] = fetch_blacklist,
    fetch_github: Callable[[], list[str]] = fetch_github_networks,
    fetch_snapd: Callable[[], list[str]] = fetch_snapd_networks,
) -> str:
    """Return the validated JSON input for ``apply_ru_firewall``."""
    missing = [
        name
        for name in (
            "RU_SERVER_ADDRESS",
            "RU_DHCP_SERVER_ADDRESS",
            "RU_DNS_ADDRESSES",
            "RU_NTP_ADDRESSES",
            "RU_LOCAL_NETWORKS",
            "RU_SERVER_TO_GITHUB_ADDRESSES",
        )
        if getattr(settings, name) is None
    ]
    if missing:
        raise FirewallInputError(
            f"Missing required RU settings for firewall input: {', '.join(missing)}"
        )
    try:
        _, _, outline_address, outline_port = parse_access_url(access_url)
    except ValueError as error:
        raise FirewallInputError(f"Invalid EU Outline access key: {error}") from None

    try:
        rkn_ipv4, rkn_ipv6 = fetch_rkn()
    except (RuntimeError, ValueError) as error:
        raise FirewallInputError(f"The RKN blacklist is unavailable: {error}") from None

    try:
        github_networks = fetch_github()
    except (RuntimeError, ValueError) as error:
        raise FirewallInputError(f"GitHub networks are unavailable: {error}") from None

    try:
        snapd_networks = fetch_snapd()
    except (RuntimeError, ValueError) as error:
        raise FirewallInputError(f"Canonical snapd networks are unavailable: {error}") from None

    assert settings.RU_SERVER_ADDRESS is not None
    ru_server = ipaddress.ip_address(settings.RU_SERVER_ADDRESS)
    for text in (*rkn_ipv4, *rkn_ipv6):
        network = ipaddress.ip_network(text, strict=False)
        if network.version == ru_server.version and ru_server in network:
            raise FirewallInputError(
                "RU_SERVER_ADDRESS is inside an RKN crawler range; the RU host "
                "would be blocked"
            )

    config_json = json.dumps(
        {
            "eu_server_address": ipaddress.ip_address(settings.EU_SERVER_ADDRESS).compressed,
            "eu_outline_address": ipaddress.ip_address(outline_address).compressed,
            "eu_outline_port": outline_port,
            "ssh_port": settings.SSH_PORT,
            "rkn_ipv4": rkn_ipv4,
            "rkn_ipv6": rkn_ipv6,
            "server_to_github_addresses": settings.RU_SERVER_TO_GITHUB_ADDRESSES,
            "snapd_networks": snapd_networks,
            "github_networks": github_networks,
            "vm_services": {
                "dns_addresses": settings.RU_DNS_ADDRESSES,
                "dhcp_server_address": settings.RU_DHCP_SERVER_ADDRESS,
                "ntp_addresses": settings.RU_NTP_ADDRESSES,
                "local_networks": settings.RU_LOCAL_NETWORKS,
            },
        }
    )
    try:
        build_ru_firewall_rules(config_json)
    except ValueError as error:
        raise FirewallInputError(f"Invalid RU firewall input: {error}") from None
    return config_json
