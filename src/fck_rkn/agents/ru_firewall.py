"""Build the RU server's plan-item-10 nftables policy."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Sequence


TABLE_NAME = "fck_rkn_ru"
RU_OUTLINE_ACCESS_PORT = 10001
EU_SERVICE_PORTS = (3128, 8443)


def _address(value: object, name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an IP address")
    try:
        return ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an IP address") from error


def _network_list(
    value: object, name: str, version: int
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must contain at least one network")
    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{name} contains a non-string network")
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError as error:
            raise ValueError(f"{name} contains an invalid network") from error
        if network.version != version:
            raise ValueError(f"{name} contains a network of the wrong IP version")
        networks.add(network)
    collapsed = ipaddress.collapse_addresses(networks)
    return sorted(
        collapsed,
        key=lambda network: (int(network.network_address), network.prefixlen),
    )


def _nft_networks(
    networks: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    return ", ".join(network.with_prefixlen for network in networks)


def _address_match(
    prefix: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address
) -> str:
    family = "ip" if address.version == 4 else "ip6"
    return f"{family} {prefix} {address.compressed}"


def _network_match(
    prefix: str, network: ipaddress.IPv4Network | ipaddress.IPv6Network
) -> str:
    family = "ip" if network.version == 4 else "ip6"
    return f"{family} {prefix} {network.with_prefixlen}"


def build_ru_firewall_rules(config_json: str) -> str:
    """Validate runtime firewall inputs and return a complete nftables table."""
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as error:
        raise ValueError("firewall configuration is not valid JSON") from error
    if not isinstance(config, dict):
        raise ValueError("firewall configuration must be a JSON object")

    client = _address(config.get("test_client_address"), "test_client_address")
    eu_server = _address(config.get("eu_server_address"), "eu_server_address")
    outline_server = _address(
        config.get("eu_outline_address"), "eu_outline_address"
    )
    outline_port = config.get("eu_outline_port")
    if (
        isinstance(outline_port, bool)
        or not isinstance(outline_port, int)
        or not 1 <= outline_port <= 65535
    ):
        raise ValueError("eu_outline_port must be between 1 and 65535")
    if outline_port in EU_SERVICE_PORTS:
        raise ValueError("eu_outline_port conflicts with a direct EU service port")
    try:
        iap_network = ipaddress.ip_network(
            config.get("iap_source_range"), strict=True
        )
    except (TypeError, ValueError) as error:
        raise ValueError("iap_source_range must be a valid CIDR network") from error

    ipv4 = _network_list(config.get("rkn_ipv4"), "rkn_ipv4", 4)
    ipv6 = _network_list(config.get("rkn_ipv6"), "rkn_ipv6", 6)

    client_allow = (
        f"    {_address_match('saddr', client)} tcp dport "
        f"{RU_OUTLINE_ACCESS_PORT} accept"
    )
    client_deny = f"    {_address_match('saddr', client)} drop"
    iap_allow = (
        f"    {_network_match('saddr', iap_network)} "
        f"tcp dport 22 accept"
    )
    iap_reply = (
        f"    {_network_match('daddr', iap_network)} tcp sport 22 "
        "ct state established,related accept"
    )
    outline_reply = (
        f"    {_address_match('saddr', outline_server)} tcp sport "
        f"{outline_port} ct state established,related accept"
    )
    crawler_rules = [
        "    ip saddr @rkn_ipv4 drop",
        "    ip6 saddr @rkn_ipv6 drop",
    ]
    direct_service_deny = (
        f"    {_address_match('daddr', eu_server)} tcp dport "
        f"{{ {', '.join(map(str, EU_SERVICE_PORTS))} }} drop"
    )
    client_egress_deny = f"    {_address_match('daddr', client)} drop"
    outline_allow = (
        f"    {_address_match('daddr', outline_server)} tcp dport "
        f"{outline_port} accept"
    )
    client_reply = (
        f"    {_address_match('daddr', client)} tcp sport "
        f"{RU_OUTLINE_ACCESS_PORT} ct state established,related accept"
    )

    return f"""destroy table inet {TABLE_NAME}
table inet {TABLE_NAME} {{
  set rkn_ipv4 {{
    type ipv4_addr
    flags interval
    elements = {{ {_nft_networks(ipv4)} }}
  }}
  set rkn_ipv6 {{
    type ipv6_addr
    flags interval
    elements = {{ {_nft_networks(ipv6)} }}
  }}
  chain input {{
    type filter hook input priority -5; policy accept;
    iifname "lo" accept
{chr(10).join(crawler_rules)}
{client_allow}
{client_deny}
{iap_allow}
    tcp dport 22 drop
{outline_reply}
  }}
  chain output {{
    type filter hook output priority -5; policy drop;
    oifname "lo" tcp dport {{ {', '.join(map(str, EU_SERVICE_PORTS))} }} accept
    oifname "lo" ct state established,related ct original proto-dst {{ {', '.join(map(str, EU_SERVICE_PORTS))} }} accept
{direct_service_deny}
{outline_allow}
{iap_reply}
{client_reply}
{client_egress_deny}
  }}
}}
"""
