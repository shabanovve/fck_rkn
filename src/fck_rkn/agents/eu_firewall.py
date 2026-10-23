"""Build the EU server's nftables policy."""

from __future__ import annotations

from fck_rkn.agents.firewall_common import (
    LOG_LIMIT,
    address_match,
    indent_rules,
    load_config,
    minimal_input_rules,
    parse_address,
    parse_minimal_vm_services,
    rkn_sets_block,
)
from fck_rkn.agents.outline import OUTLINE_ACCESS_KEY_PORT


TABLE_NAME = "fck_rkn_eu"
DEFAULT_SSH_PORT = 22


def _log(reason: str) -> str:
    return (
        f'counter {LOG_LIMIT} log prefix "fck-rkn eu {reason}: " level info'
    )


def build_eu_firewall_rules(config_json: str) -> str:
    """Validate runtime firewall inputs and return a complete nftables table.

    Incoming traffic is denied by default; outgoing traffic is unrestricted.
    Ports published by Docker containers reach the host through the forward
    hook, so denied external connections to them are handled there.
    """
    config = load_config(config_json)
    ru_server = parse_address(config.get("ru_server_address"), "ru_server_address")
    ssh_port = config.get("ssh_port", DEFAULT_SSH_PORT)
    if (
        isinstance(ssh_port, bool)
        or not isinstance(ssh_port, int)
        or not 1 <= ssh_port <= 65535
    ):
        raise ValueError("ssh_port must be between 1 and 65535")
    rkn_sets = rkn_sets_block(config)
    vm_services = parse_minimal_vm_services(config.get("vm_services"))

    input_rules = [
        *minimal_input_rules(vm_services),
        f"ip saddr @rkn_ipv4 {_log('input crawler-v4')} drop",
        f"ip6 saddr @rkn_ipv6 {_log('input crawler-v6')} drop",
        f"tcp dport {ssh_port} accept",
        f"{address_match('saddr', ru_server)} meta l4proto {{ tcp, udp }} "
        f"th dport {OUTLINE_ACCESS_KEY_PORT} accept",
        f"{_log('input denied')} drop",
    ]
    forward_rules = [
        "ct state established,related accept",
        f"ct status dnat {_log('forward direct-service')} drop",
    ]

    return f"""destroy table inet {TABLE_NAME}
table inet {TABLE_NAME} {{
{rkn_sets}
  chain input {{
    type filter hook input priority -5; policy drop;
{indent_rules(input_rules)}
  }}
  chain forward {{
    type filter hook forward priority -5; policy accept;
{indent_rules(forward_rules)}
  }}
}}
"""
