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
        "iap_source_range": "35.235.240.0/20",
        "rkn_ipv4": ["192.0.2.0/24"],
        "rkn_ipv6": ["2001:db8::/32"],
    }
    config.update(overrides)
    return json.dumps(config)


class RuFirewallRuleTests(unittest.TestCase):
    def test_builds_only_the_defined_ru_traffic_policy(self) -> None:
        rules = build_ru_firewall_rules(valid_config())

        self.assertIn("destroy table inet fck_rkn_ru", rules)
        self.assertIn("ip saddr @rkn_ipv4 drop", rules)
        self.assertIn("ip6 saddr @rkn_ipv6 drop", rules)
        self.assertIn("ip saddr 10.0.0.3 tcp dport 10001 accept", rules)
        self.assertIn("ip saddr 10.0.0.3 drop", rules)
        self.assertIn(
            'oifname "lo" tcp dport { 3128, 8443 } accept', rules
        )
        self.assertIn(
            'oifname "lo" ct state established,related ct original proto-dst '
            "{ 3128, 8443 } accept",
            rules,
        )
        self.assertIn("ip saddr 35.235.240.0/20 tcp dport 22 accept", rules)
        self.assertIn("tcp dport 22 drop", rules)
        self.assertIn("ip daddr 10.0.0.2 tcp dport 10001 accept", rules)
        self.assertIn("ip daddr 10.0.0.2 tcp dport { 3128, 8443 } drop", rules)
        self.assertIn("ip daddr 10.0.0.3 drop", rules)
        self.assertIn("policy accept;", rules)
        self.assertIn("policy drop;", rules)
        self.assertNotIn("    ct state established,related accept\n", rules)

    def test_keeps_ipv6_inputs_and_normalizes_networks(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(
                test_client_address="2001:db8:1::2",
                eu_server_address="2001:db8:2::1",
                eu_outline_address="2001:db8:2::1",
                iap_source_range="2001:db8:3::/64",
                rkn_ipv4=["198.51.100.0/24"],
                rkn_ipv6=["2001:db8:ffff::/48"],
            )
        )

        self.assertIn("ip6 saddr 2001:db8:1::2 tcp dport 10001 accept", rules)
        self.assertIn("ip6 saddr 2001:db8:3::/64 tcp dport 22 accept", rules)
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
            {"iap_source_range": "35.235.240.0/19"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(**changes))

    def test_rejects_non_object_json(self) -> None:
        for content in ("not-json", "[]", "null"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                build_ru_firewall_rules(content)


if __name__ == "__main__":
    unittest.main()
