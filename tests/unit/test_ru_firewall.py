from __future__ import annotations

import json
import unittest

from fck_rkn.agents.ru_firewall import build_ru_firewall_rules


def valid_config(**overrides: object) -> str:
    config: dict[str, object] = {
        "test_client_address": "10.0.0.3",
        "eu_server_address": "10.0.0.2",
        "eu_outline_address": "10.0.0.2",
        "eu_outline_port": 10001,
        "rkn_ipv4": ["192.0.2.0/24"],
        "rkn_ipv6": ["2001:db8::/32"],
        "vm_services": {
            "metadata_address": "169.254.169.254",
            "dns_addresses": ["169.254.169.254"],
            "dhcp_server_address": "169.254.169.254",
            "ntp_addresses": ["169.254.169.254"],
            "local_networks": ["10.0.0.0/24"],
        },
    }
    config.update(overrides)
    return json.dumps(config)


class RuFirewallRuleTests(unittest.TestCase):
    def test_builds_only_the_defined_ru_traffic_policy(self) -> None:
        rules = build_ru_firewall_rules(valid_config())

        self.assertIn("destroy table inet fck_rkn_ru", rules)
        self.assertIn(
            'ip saddr @rkn_ipv4 counter limit rate 10/second burst 20 packets '
            'log prefix "fck-rkn ru input crawler-v4: " level info accept',
            rules,
        )
        self.assertIn(
            'ip6 saddr @rkn_ipv6 counter limit rate 10/second burst 20 packets '
            'log prefix "fck-rkn ru input crawler-v6: " level info accept',
            rules,
        )
        self.assertIn("ip saddr 10.0.0.3 tcp dport 10001 accept", rules)
        self.assertIn(
            'ip saddr 10.0.0.3 counter limit rate 10/second burst 20 packets '
            'log prefix "fck-rkn ru input test-client: " level info accept',
            rules,
        )
        self.assertIn(
            'oifname "lo" tcp dport { 3128, 8443, 10000, 10001 } accept',
            rules,
        )
        self.assertIn(
            'oifname "lo" ct state established,related ct original proto-dst '
            "{ 3128, 8443, 10000, 10001 } accept",
            rules,
        )
        self.assertIn(
            "ip daddr 169.254.169.254 tcp dport { 80, 443 } accept", rules
        )
        self.assertIn("    tcp dport 22 accept", rules)
        self.assertIn("tcp sport 22 ct state established,related accept", rules)
        self.assertNotIn("tcp dport 22 drop", rules)
        self.assertIn("ip daddr 10.0.0.2 tcp dport 10001 accept", rules)
        self.assertIn(
            'ip daddr 10.0.0.2 tcp dport { 3128, 8443 } counter limit '
            'rate 10/second burst 20 packets log prefix '
            '"fck-rkn ru output direct-eu-service: " level info accept',
            rules,
        )
        self.assertIn(
            'ip daddr 10.0.0.3 counter limit rate 10/second burst 20 packets '
            'log prefix "fck-rkn ru output test-client: " level info accept',
            rules,
        )
        self.assertIn(
            'counter limit rate 10/second burst 20 packets log prefix '
            '"fck-rkn ru output default-policy: " level info',
            rules,
        )
        self.assertIn("policy accept;", rules)
        self.assertNotIn(" drop", rules)
        self.assertIn("    ct state established,related accept\n", rules)

    def test_ssh_is_allowed_from_all_sources_before_crawler_logging(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(
                rkn_ipv4=["35.235.240.0/20"],
                rkn_ipv6=["2001:db8::/32"],
            )
        )
        ssh_allow = "tcp dport 22 accept"
        crawler_log = 'ip saddr @rkn_ipv4 counter limit rate 10/second'

        self.assertLess(rules.index(ssh_allow), rules.index(crawler_log))
        self.assertNotIn(" drop", rules)

    def test_keeps_ipv6_inputs_and_normalizes_networks(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(
                test_client_address="2001:db8:1::2",
                eu_server_address="2001:db8:2::1",
                eu_outline_address="2001:db8:2::1",
                rkn_ipv4=["198.51.100.0/24"],
                rkn_ipv6=["2001:db8:ffff::/48"],
            )
        )

        self.assertIn("ip6 saddr 2001:db8:1::2 tcp dport 10001 accept", rules)
        self.assertIn("tcp dport 22 accept", rules)
        self.assertIn("ip6 daddr 2001:db8:2::1 tcp dport 10001 accept", rules)

    def test_rejects_missing_or_invalid_blacklists(self) -> None:
        for changes in (
            {"rkn_ipv4": []},
            {"rkn_ipv6": []},
            {"rkn_ipv4": ["not-a-network"]},
            {"rkn_ipv6": ["192.0.2.0/24"]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(**changes))

    def test_rejects_invalid_addresses_ports_and_iap_ranges(self) -> None:
        for changes in (
            {"test_client_address": "client-name"},
            {"eu_server_address": "eu-name"},
            {"eu_outline_address": "outline-name"},
            {"eu_outline_port": 0},
            {"eu_outline_port": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(**changes))

    def test_rejects_non_object_json(self) -> None:
        for content in ("not-json", "[]", "null"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                build_ru_firewall_rules(content)


if __name__ == "__main__":
    unittest.main()


class RuMinimalVmServiceTests(unittest.TestCase):
    def chain(self, rules: str, name: str) -> str:
        start = rules.index(f"chain {name}")
        return rules[start : rules.index("\n  }", start)]

    def test_minimal_vm_services_come_first_in_both_chains(self) -> None:
        rules = build_ru_firewall_rules(valid_config())
        input_chain = self.chain(rules, "input")
        output_chain = self.chain(rules, "output")

        for text in (
            'iifname "lo" accept',
            "ct state established,related accept",
            "ip saddr 169.254.169.254 udp sport 67 udp dport 68 accept",
            "icmp type { echo-request",
        ):
            self.assertIn(text, input_chain)
        for text in (
            'oifname "lo" accept',
            "ct state established,related accept",
            "th dport 53 accept",
            "tcp dport { 80, 443 } accept",
            "udp sport 68 udp dport 67 accept",
            "udp dport 123 accept",
            "ip daddr 10.0.0.0/24 icmp type",
        ):
            self.assertIn(text, output_chain)
        self.assertLess(
            input_chain.index("udp dport 68"), input_chain.index("tcp dport 22")
        )
        self.assertLess(
            input_chain.index("icmp type"), input_chain.index("@rkn_ipv4")
        )
        self.assertLess(
            output_chain.index("udp dport 123"),
            output_chain.index("direct-eu-service"),
        )

    def test_services_use_the_supplied_provider_addresses(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(
                vm_services={
                    "metadata_address": "192.0.2.10",
                    "dns_addresses": ["192.0.2.11", "2001:db8::53"],
                    "dhcp_server_address": "192.0.2.12",
                    "ntp_addresses": ["192.0.2.13"],
                    "local_networks": ["198.51.100.0/24", "2001:db8:1::/64"],
                }
            )
        )

        self.assertIn("ip daddr { 192.0.2.11 } meta l4proto", rules)
        self.assertIn("ip6 daddr { 2001:db8::53 } meta l4proto", rules)
        self.assertIn("ip daddr 192.0.2.10 tcp dport { 80, 443 } accept", rules)
        self.assertIn("ip daddr { 192.0.2.12, 255.255.255.255 }", rules)
        self.assertIn("ip daddr { 192.0.2.13 } udp dport 123 accept", rules)
        self.assertIn("ip6 daddr 2001:db8:1::/64 icmpv6 type", rules)
        self.assertNotIn("169.254.169.254", rules)

    def test_rejects_missing_or_invalid_vm_services(self) -> None:
        good = json.loads(valid_config())["vm_services"]
        changes = (
            None,
            {**good, "metadata_address": "metadata"},
            {**good, "dns_addresses": []},
            {**good, "ntp_addresses": ["ntp"]},
            {**good, "dhcp_server_address": "2001:db8::1"},
            {**good, "local_networks": []},
            {**good, "local_networks": ["10.0.0.1/24"]},
        )
        for vm_services in changes:
            with self.subTest(vm_services=vm_services), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(vm_services=vm_services))
