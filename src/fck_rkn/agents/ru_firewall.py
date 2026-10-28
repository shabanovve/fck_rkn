"""Build the RU server's nftables policy."""

from __future__ import annotations

import ipaddress

from fck_rkn.agents.firewall_common import (
    LOG_LIMIT,
    address_match,
    indent_rules,
    load_config,
    minimal_input_rules,
    minimal_output_rules,
    parse_address,
    parse_minimal_vm_services,
    rkn_networks,
    rkn_sets_block,
    service_sets_block,
)


TABLE_NAME = "fck_rkn_ru"
RU_OUTLINE_ACCESS_PORT = 10001
RU_OUTLINE_API_PORT = 10000
EU_SERVICE_PORTS = (3128, 8443)
DEFAULT_EU_SSH_PORT = 22
SERVER_TO_GITHUB_PORTS = (80, 443)
SNAPD_PORT = 443


def _log(reason: str) -> str:
    return f'counter {LOG_LIMIT} log prefix "fck-rkn ru {reason}: " level info'


def build_ru_firewall_rules(config_json: str) -> str:
    """Validate runtime firewall inputs and return a complete nftables table.

    Incoming and outgoing traffic is denied by default. A rate-limited log
    rule is separate from its drop rule, so packets over the log rate limit
    are still dropped instead of falling through to later allow rules.

    VPN clients use Shadowsocks, which does not forward packets: their
    requests leave the Outline Server process through the output hook. Only
    loopback (including the local web-proxy and MTProto forwards to EU) and
    the Outline transport to EU may leave ``ru-server``. The forward hook
    denies anything routed through the host.
    """
    config = load_config(config_json)
    eu_server = parse_address(config.get("eu_server_address"), "eu_server_address")
    outline_server = parse_address(
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
    ssh_port = config.get("ssh_port", DEFAULT_EU_SSH_PORT)
    if (
        isinstance(ssh_port, bool)
        or not isinstance(ssh_port, int)
        or not 1 <= ssh_port <= 65535
    ):
        raise ValueError("ssh_port must be between 1 and 65535")
    rkn_sets = rkn_sets_block(config)
    crawlers = rkn_networks(config)
    server_to_github_sets = service_sets_block(config, "server_to_github_addresses", "server_to_github", crawlers)
    snapd_sets = service_sets_block(config, "snapd_networks", "snapd", crawlers)
    github_sets = service_sets_block(config, "github_networks", "github", crawlers)
    service_networks = [
        ipaddress.ip_network(item)
        for key in ("server_to_github_addresses", "snapd_networks", "github_networks")
        for item in config[key]
    ]
    for protected in (eu_server, outline_server):
        if any(
            protected.version == network.version and protected in network
            for network in service_networks
        ):
            raise ValueError(
                "Provider or snapd networks must not contain an EU server address"
            )
    vm_services = parse_minimal_vm_services(config.get("vm_services"))

    eu_service_ports = ", ".join(map(str, EU_SERVICE_PORTS))
    direct_eu_match = (
        f"{address_match('daddr', eu_server)} tcp dport {{ {eu_service_ports} }}"
    )
    server_to_github_ports = ", ".join(map(str, SERVER_TO_GITHUB_PORTS))
    service_output_rules = []
    for family in ("ipv4", "ipv6"):
        match = "ip" if family == "ipv4" else "ip6"
        if f"set server_to_github_{family} " in server_to_github_sets:
            service_output_rules.append(
                f"{match} daddr @server_to_github_{family} tcp dport {{ {server_to_github_ports} }} accept"
            )
        if f"set snapd_{family} " in snapd_sets:
            service_output_rules.append(
                f"{match} daddr @snapd_{family} tcp dport {SNAPD_PORT} accept"
            )
        if f"set github_{family} " in github_sets:
            service_output_rules.append(
                f"{match} daddr @github_{family} tcp dport 443 accept"
            )
    input_rules = [
        *minimal_input_rules(vm_services),
        f"ip saddr @rkn_ipv4 {_log('input crawler-v4')}",
        "ip saddr @rkn_ipv4 drop",
        f"ip6 saddr @rkn_ipv6 {_log('input crawler-v6')}",
        "ip6 saddr @rkn_ipv6 drop",
        f"tcp dport {ssh_port} accept",
        f"meta l4proto {{ tcp, udp }} th dport {RU_OUTLINE_ACCESS_PORT} accept",
        f"tcp dport {RU_OUTLINE_API_PORT} accept",
        _log("input denied"),
        "drop",
    ]
    output_rules = [
        *minimal_output_rules(vm_services),
        *service_output_rules,
        f"{direct_eu_match} {_log('output direct-eu-service')}",
        f"{direct_eu_match} drop",
        f"{address_match('daddr', outline_server)} tcp dport {outline_port} accept",
        _log("output denied"),
        "drop",
    ]
    forward_rules = [
        "ct state established,related accept",
        _log("forward denied"),
        "drop",
    ]

    return f"""destroy table inet {TABLE_NAME}
table inet {TABLE_NAME} {{
{rkn_sets}
{server_to_github_sets}
{snapd_sets}
{github_sets}
  chain input {{
    type filter hook input priority -5; policy drop;
{indent_rules(input_rules)}
  }}
  chain output {{
    type filter hook output priority -5; policy drop;
{indent_rules(output_rules)}
  }}
  chain forward {{
    type filter hook forward priority -5; policy drop;
{indent_rules(forward_rules)}
  }}
}}
"""
