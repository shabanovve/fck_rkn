"""Build the RU server's plan-item-10 nftables policy."""

from __future__ import annotations

from fck_rkn.agents.firewall_common import (
    LOG_LIMIT,
    address_match,
    load_config,
    rkn_sets_block,
    minimal_input_rules,
    minimal_output_rules,
    parse_address,
    parse_minimal_vm_services,
)


TABLE_NAME = "fck_rkn_ru"
RU_OUTLINE_ACCESS_PORT = 10001
RU_OUTLINE_API_PORT = 10000
RU_OUTLINE_LOCAL_PORTS = (RU_OUTLINE_API_PORT, RU_OUTLINE_ACCESS_PORT)
EU_SERVICE_PORTS = (3128, 8443)


def build_ru_firewall_rules(config_json: str) -> str:
    """Validate runtime firewall inputs and return a complete nftables table."""
    config = load_config(config_json)

    client = parse_address(config.get("test_client_address"), "test_client_address")
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
    rkn_sets = rkn_sets_block(config)
    vm_services = parse_minimal_vm_services(config.get("vm_services"))
    minimal_input = "\n".join(f"    {rule}" for rule in minimal_input_rules(vm_services))
    minimal_output = "\n".join(
        f"    {rule}" for rule in minimal_output_rules(vm_services)
    )

    client_allow = (
        f"    {address_match('saddr', client)} tcp dport "
        f"{RU_OUTLINE_ACCESS_PORT} accept"
    )
    client_log = (
        f'    {address_match("saddr", client)} counter {LOG_LIMIT} '
        'log prefix "fck-rkn ru input test-client: " level info accept'
    )
    ssh_reply = "    tcp sport 22 ct state established,related accept"
    outline_reply = (
        f"    {address_match('saddr', outline_server)} tcp sport "
        f"{outline_port} ct state established,related accept"
    )
    crawler_rules = [
        f'    ip saddr @rkn_ipv4 counter {LOG_LIMIT} '
        'log prefix "fck-rkn ru input crawler-v4: " level info accept',
        f'    ip6 saddr @rkn_ipv6 counter {LOG_LIMIT} '
        'log prefix "fck-rkn ru input crawler-v6: " level info accept',
    ]
    direct_service_log = (
        f'    {address_match("daddr", eu_server)} tcp dport '
        f'{{ {", ".join(map(str, EU_SERVICE_PORTS))} }} counter {LOG_LIMIT} '
        'log prefix "fck-rkn ru output direct-eu-service: " level info accept'
    )
    client_egress_log = (
        f'    {address_match("daddr", client)} counter {LOG_LIMIT} '
        'log prefix "fck-rkn ru output test-client: " level info accept'
    )
    output_default_log = (
        f'    counter {LOG_LIMIT} log prefix '
        '"fck-rkn ru output default-policy: " level info'
    )
    outline_allow = (
        f"    {address_match('daddr', outline_server)} tcp dport "
        f"{outline_port} accept"
    )
    client_reply = (
        f"    {address_match('daddr', client)} tcp sport "
        f"{RU_OUTLINE_ACCESS_PORT} ct state established,related accept"
    )

    return f"""destroy table inet {TABLE_NAME}
table inet {TABLE_NAME} {{
{rkn_sets}
  chain input {{
    type filter hook input priority -5; policy accept;
{minimal_input}
    tcp dport 22 accept
{chr(10).join(crawler_rules)}
{client_allow}
{client_log}
{outline_reply}
  }}
  chain output {{
    type filter hook output priority -5; policy accept;
{minimal_output}
    oifname "lo" tcp dport {{ {', '.join(map(str, (*EU_SERVICE_PORTS, *RU_OUTLINE_LOCAL_PORTS)))} }} accept
    oifname "lo" ct state established,related ct original proto-dst {{ {', '.join(map(str, (*EU_SERVICE_PORTS, *RU_OUTLINE_LOCAL_PORTS)))} }} accept
{direct_service_log}
{outline_allow}
{ssh_reply}
{client_reply}
{client_egress_log}
{output_default_log}
  }}
}}
"""
