"""Firewall building blocks shared by the RU and EU server agents."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Sequence
from dataclasses import dataclass

from fck_rkn.remote import render

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DHCPV4_BROADCAST = "255.255.255.255"
DHCPV6_SERVERS = "ff02::1:2"
ICMP_V4_TYPES = "echo-request, echo-reply, destination-unreachable, time-exceeded"
ICMP_V6_TYPES = ICMP_V4_TYPES.replace("destination-unreachable", "destination-unreachable, packet-too-big")
LOG_LIMIT = "limit rate 10/second burst 20 packets"
ND_TYPES = "nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert"


def parse_address(value: object, name: str) -> IPAddress:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an IP address")
    try:
        return ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an IP address") from error


def address_match(prefix: str, address: IPAddress | IPNetwork) -> str:
    family = "ip" if address.version == 4 else "ip6"
    text = (
        address.with_prefixlen
        if isinstance(address, (ipaddress.IPv4Network, ipaddress.IPv6Network))
        else address.compressed
    )
    return f"{family} {prefix} {text}"


def _address_list(value: object, name: str) -> list[IPAddress]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must contain at least one IP address")
    return [parse_address(item, name) for item in value]


def _address_set(addresses: list[IPAddress], prefix: str) -> list[str]:
    """Return one nftables match per IP version for a list of addresses."""
    rules = []
    for version, family in ((4, "ip"), (6, "ip6")):
        members = [a.compressed for a in addresses if a.version == version]
        if members:
            rules.append(f"{family} {prefix} {{ {', '.join(members)} }}")
    return rules


@dataclass(frozen=True)
class MinimalVmServices:
    """Provider-specific addresses of the services a VM needs to stay healthy."""

    metadata_address: IPAddress
    dns_addresses: list[IPAddress]
    dhcp_server_address: ipaddress.IPv4Address
    ntp_addresses: list[IPAddress]
    local_networks: list[IPNetwork]


def parse_minimal_vm_services(config: object) -> MinimalVmServices:
    """Validate the ``vm_services`` section of a firewall configuration."""
    if not isinstance(config, dict):
        raise ValueError("vm_services must be a JSON object")
    dhcp = parse_address(config.get("dhcp_server_address"), "dhcp_server_address")
    if dhcp.version != 4:
        raise ValueError("dhcp_server_address must be an IPv4 address")
    networks = config.get("local_networks")
    if not isinstance(networks, list) or not networks:
        raise ValueError("local_networks must contain at least one network")
    parsed_networks: list[IPNetwork] = []
    for item in networks:
        if not isinstance(item, str):
            raise ValueError("local_networks contains a non-string network")
        try:
            parsed_networks.append(ipaddress.ip_network(item, strict=True))
        except ValueError as error:
            raise ValueError("local_networks contains an invalid network") from error
    return MinimalVmServices(
        metadata_address=parse_address(
            config.get("metadata_address"), "metadata_address"
        ),
        dns_addresses=_address_list(config.get("dns_addresses"), "dns_addresses"),
        dhcp_server_address=dhcp,
        ntp_addresses=_address_list(config.get("ntp_addresses"), "ntp_addresses"),
        local_networks=parsed_networks,
    )


def minimal_input_rules(services: MinimalVmServices) -> list[str]:
    """Rules, in order, that keep a VM healthy for its input chain."""
    rules = [
        'iifname "lo" accept',
        "ct state established,related accept",
        f"{address_match('saddr', services.dhcp_server_address)} "
        "udp sport 67 udp dport 68 accept",
        "ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept",
        f"icmpv6 type {{ {ND_TYPES} }} accept",
    ]
    for network in services.local_networks:
        types = ICMP_V4_TYPES if network.version == 4 else ICMP_V6_TYPES
        protocol = "icmp" if network.version == 4 else "icmpv6"
        rules.append(
            f"{address_match('saddr', network)} {protocol} type {{ {types} }} accept"
        )
    return rules


def minimal_output_rules(services: MinimalVmServices) -> list[str]:
    """Rules, in order, that keep a VM healthy for its output chain."""
    metadata = services.metadata_address
    rules = [
        'oifname "lo" accept',
        "ct state established,related accept",
    ]
    rules += [
        f"{match} meta l4proto {{ tcp, udp }} th dport 53 accept"
        for match in _address_set(services.dns_addresses, "daddr")
    ]
    rules += [
        f"{address_match('daddr', metadata)} tcp dport {{ 80, 443 }} accept",
        f"ip daddr {{ {services.dhcp_server_address.compressed}, "
        f"{DHCPV4_BROADCAST} }} udp sport 68 udp dport 67 accept",
        f"ip6 daddr {DHCPV6_SERVERS} udp sport 546 udp dport 547 accept",
    ]
    rules += [
        f"{match} udp dport 123 accept"
        for match in _address_set(services.ntp_addresses, "daddr")
    ]
    rules.append(f"icmpv6 type {{ {ND_TYPES} }} accept")
    for network in services.local_networks:
        types = ICMP_V4_TYPES if network.version == 4 else ICMP_V6_TYPES
        protocol = "icmp" if network.version == 4 else "icmpv6"
        rules.append(
            f"{address_match('daddr', network)} {protocol} type {{ {types} }} accept"
        )
    return rules


def load_config(config_json: str) -> dict:
    """Parse a firewall configuration, which must be a JSON object."""
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as error:
        raise ValueError("firewall configuration is not valid JSON") from error
    if not isinstance(config, dict):
        raise ValueError("firewall configuration must be a JSON object")
    return config


def _network_list(value: object, name: str, version: int) -> list[IPNetwork]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must contain at least one network")
    networks: set[IPNetwork] = set()
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


def _nft_networks(networks: Sequence[IPNetwork]) -> str:
    return ", ".join(network.with_prefixlen for network in networks)


def rkn_sets_block(config: dict) -> str:
    """Validate the RKN crawler ranges and return the nftables set definitions."""
    ipv4 = _network_list(config.get("rkn_ipv4"), "rkn_ipv4", 4)
    ipv6 = _network_list(config.get("rkn_ipv6"), "rkn_ipv6", 6)
    return f"""  set rkn_ipv4 {{
    type ipv4_addr
    flags interval
    elements = {{ {_nft_networks(ipv4)} }}
  }}
  set rkn_ipv6 {{
    type ipv6_addr
    flags interval
    elements = {{ {_nft_networks(ipv6)} }}
  }}"""


def indent_rules(rules: Sequence[str]) -> str:
    return "\n".join(f"    {rule}" for rule in rules)


def firewall_apply_command(host: str) -> str:
    """Return the transactional installer script for ``host`` ("ru" or "eu")."""
    return render(
        "apply_firewall.sh",
        host=host,
        rules_file=f"/etc/fck-rkn/{host}-firewall.nft",
        unit_file=f"/etc/systemd/system/fck-rkn-{host}-firewall.service",
        lock_file=f"/run/lock/fck-rkn-{host}-firewall.lock",
    )
