"""Production EU firewall input, built from validated prod.env settings."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable

from fck_rkn.agents.eu_firewall import build_eu_firewall_rules
from fck_rkn.agents.rkn_blacklist import fetch_blacklist
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
                "metadata_address": settings.EU_METADATA_ADDRESS,
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
