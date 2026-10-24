from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest

from fck_rkn.agents.ru_firewall import build_ru_firewall_rules

LOG = "counter limit rate 10/second burst 20 packets log prefix"


def valid_config(**overrides: object) -> str:
    config: dict[str, object] = {
        "eu_server_address": "10.0.0.2",
        "eu_outline_address": "10.0.0.2",
        "eu_outline_port": 10001,
        "rkn_ipv4": ["192.0.2.0/24"],
        "rkn_ipv6": ["2001:db8::/32"],
        "provider_networks": ["198.51.100.0/24", "2001:db9:1::/48"],
        "snapd_networks": ["203.0.113.0/24"],
        "github_networks": ["198.51.100.0/24", "2001:db9:2::/48"],
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


def chain(rules: str, name: str) -> str:
    start = rules.index(f"chain {name}")
    return rules[start : rules.index("\n  }", start)]


class RuServiceRuleTests(unittest.TestCase):
    def test_provider_and_snapd_are_allowed_outward_after_minimal_rules(self) -> None:
        output = chain(build_ru_firewall_rules(valid_config()), "output")

        self.assertIn("ip daddr @provider_ipv4 tcp dport { 80, 443 } accept", output)
        self.assertIn("ip6 daddr @provider_ipv6 tcp dport { 80, 443 } accept", output)
        self.assertIn("ip daddr @snapd_ipv4 tcp dport 443 accept", output)
        self.assertIn("ip daddr @github_ipv4 tcp dport 443 accept", output)
        self.assertIn("ip6 daddr @github_ipv6 tcp dport 443 accept", output)
        self.assertNotIn("@snapd_ipv6", output)
        self.assertLess(
            output.index("169.254.169.254 tcp dport { 80, 443 } accept"),
            output.index("@provider_ipv4"),
        )
        self.assertLess(
            output.index("@snapd_ipv4"), output.index("fck-rkn ru output denied")
        )
        self.assertLess(
            output.index("@provider_ipv4"), output.index("direct-eu-service")
        )

    def test_service_sets_contain_the_supplied_ranges(self) -> None:
        rules = build_ru_firewall_rules(valid_config())

        self.assertIn("set provider_ipv4", rules)
        self.assertIn("198.51.100.0/24", rules)
        self.assertIn("set provider_ipv6", rules)
        self.assertIn("set snapd_ipv4", rules)
        self.assertNotIn("set snapd_ipv6", rules)
        self.assertIn("set github_ipv4", rules)
        self.assertIn("set github_ipv6", rules)

    def test_invalid_service_ranges_are_rejected(self) -> None:
        cases = {
            "missing": {"provider_networks": None},
            "empty": {"snapd_networks": []},
            "invalid": {"provider_networks": ["not-a-network"]},
            "non-string": {"snapd_networks": [5]},
            "crawler overlap": {"provider_networks": ["192.0.2.128/25"]},
            "ipv6 crawler overlap": {"snapd_networks": ["2001:db8:5::/48"]},
            "covers eu": {"provider_networks": ["10.0.0.0/24"]},
            "too many": {
                "snapd_networks": [f"203.0.{i // 256}.{i % 256}/32" for i in range(2001)]
            },
        }
        for name, override in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                config = json.loads(valid_config())
                for key, value in override.items():
                    if value is None:
                        del config[key]
                    else:
                        config[key] = value
                build_ru_firewall_rules(json.dumps(config))


class RuFirewallRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = build_ru_firewall_rules(valid_config())
        self.input = chain(self.rules, "input")
        self.output = chain(self.rules, "output")
        self.forward = chain(self.rules, "forward")

    def test_every_chain_is_default_deny(self) -> None:
        self.assertEqual(self.rules.count("policy drop;"), 3)
        self.assertNotIn("policy accept", self.rules)
        for text in (self.input, self.output, self.forward):
            self.assertTrue(text.rstrip().endswith("drop"))

    def test_incoming_allows_ssh_and_vpn_from_any_source(self) -> None:
        self.assertIn("    tcp dport 22 accept", self.input)
        self.assertIn(
            "    meta l4proto { tcp, udp } th dport 10001 accept", self.input
        )
        self.assertNotIn("10.0.0.3", self.rules)

    def test_crawlers_are_logged_and_dropped_before_ssh_and_vpn(self) -> None:
        for family, name in (("ip", "v4"), ("ip6", "v6")):
            log = (
                f'{family} saddr @rkn_ip{name} {LOG} '
                f'"fck-rkn ru input crawler-{name}: " level info'
            )
            drop = f"{family} saddr @rkn_ip{name} drop"
            self.assertIn(log, self.input)
            self.assertLess(self.input.index(log), self.input.index(drop))
            self.assertLess(self.input.index(drop), self.input.index("tcp dport 22"))
            self.assertLess(
                self.input.index(drop), self.input.index("th dport 10001")
            )

    def test_log_rules_never_carry_the_verdict(self) -> None:
        for line in self.rules.splitlines():
            if "log prefix" in line:
                self.assertNotIn(" drop", line)
                self.assertNotIn(" accept", line)

    def test_other_incoming_traffic_is_logged_and_denied(self) -> None:
        self.assertTrue(
            self.input.rstrip().endswith(
                f'{LOG} "fck-rkn ru input denied: " level info\n    drop'
            )
        )

    def test_outgoing_allows_only_minimal_services_and_the_eu_transport(
        self,
    ) -> None:
        self.assertIn("ip daddr 10.0.0.2 tcp dport 10001 accept", self.output)
        self.assertIn('oifname "lo" accept', self.output)
        accepts = [
            line for line in self.output.splitlines() if line.endswith(" accept")
        ]
        self.assertEqual(len(accepts), 15)
        self.assertTrue(
            self.output.rstrip().endswith(
                f'{LOG} "fck-rkn ru output denied: " level info\n    drop'
            )
        )

    def test_direct_eu_proxy_ports_are_logged_and_denied(self) -> None:
        log = (
            f'ip daddr 10.0.0.2 tcp dport {{ 3128, 8443 }} {LOG} '
            '"fck-rkn ru output direct-eu-service: " level info'
        )
        drop = "ip daddr 10.0.0.2 tcp dport { 3128, 8443 } drop"

        self.assertIn(log, self.output)
        self.assertLess(self.output.index(log), self.output.index(drop))
        self.assertLess(
            self.output.index(drop), self.output.index("tcp dport 10001 accept")
        )

    def test_forwarded_traffic_is_denied_and_logged(self) -> None:
        self.assertIn("ct state established,related accept", self.forward)
        self.assertIn('"fck-rkn ru forward denied: "', self.forward)
        self.assertEqual(
            [line.strip() for line in self.forward.splitlines()[-1:]], ["drop"]
        )

    def test_has_no_log_only_or_client_special_rules(self) -> None:
        self.assertNotIn("test-client", self.rules)
        self.assertNotIn("default-policy", self.rules)
        self.assertNotIn("test_client_address", self.rules)

    def test_test_client_address_is_not_required_or_used(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(test_client_address="10.0.0.3", other_host_address="1.1.1.1")
        )

        self.assertEqual(rules, self.rules)

    def test_ssh_port_can_be_supplied(self) -> None:
        rules = build_ru_firewall_rules(valid_config(ssh_port=2222))

        self.assertIn("    tcp dport 2222 accept", rules)
        self.assertNotIn("tcp dport 22 accept", rules)

    def test_keeps_ipv6_inputs(self) -> None:
        rules = build_ru_firewall_rules(
            valid_config(
                eu_server_address="2001:db8:2::1",
                eu_outline_address="2001:db8:2::1",
                rkn_ipv4=["192.0.2.0/24"],
                rkn_ipv6=["2001:db8:ffff::/48"],
            )
        )

        self.assertIn("ip6 daddr 2001:db8:2::1 tcp dport 10001 accept", rules)
        self.assertIn(
            "ip6 daddr 2001:db8:2::1 tcp dport { 3128, 8443 } drop", rules
        )
        self.assertIn("elements = { 2001:db8:ffff::/48 }", rules)

    def test_rejects_missing_or_invalid_blacklists(self) -> None:
        for changes in (
            {"rkn_ipv4": []},
            {"rkn_ipv6": []},
            {"rkn_ipv4": ["not-a-network"]},
            {"rkn_ipv6": ["192.0.2.0/24"]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(**changes))

    def test_rejects_invalid_addresses_and_ports(self) -> None:
        for changes in (
            {"eu_server_address": "eu-name"},
            {"eu_outline_address": "outline-name"},
            {"eu_outline_port": 0},
            {"eu_outline_port": True},
            {"eu_outline_port": 3128},
            {"ssh_port": 0},
            {"ssh_port": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ru_firewall_rules(valid_config(**changes))

    def test_rejects_non_object_json(self) -> None:
        for content in ("not-json", "[]", "null"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                build_ru_firewall_rules(content)

    @unittest.skipUnless(shutil.which("nft") and shutil.which("unshare"), "nft missing")
    def test_ruleset_is_accepted_by_nft(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".nft") as handle:
            handle.write(self.rules)
            handle.flush()
            result = subprocess.run(
                ["unshare", "-Urn", "nft", "-c", "-f", handle.name],
                capture_output=True,
                text=True,
            )
        if "Operation not permitted" in result.stderr:
            self.skipTest("network namespaces unavailable")
        self.assertEqual(result.returncode, 0, result.stderr)


class RuMinimalVmServiceTests(unittest.TestCase):
    def test_minimal_vm_services_come_first_in_both_chains(self) -> None:
        rules = build_ru_firewall_rules(valid_config())
        input_chain = chain(rules, "input")
        output_chain = chain(rules, "output")

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
            input_chain.index("udp dport 68"), input_chain.index("@rkn_ipv4")
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


if __name__ == "__main__":
    unittest.main()
