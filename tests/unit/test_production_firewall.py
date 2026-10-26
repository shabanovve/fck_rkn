from __future__ import annotations

import json
import unittest
from pathlib import Path

from fck_rkn import production_firewall
from fck_rkn.agents.eu_firewall import build_eu_firewall_rules
from fck_rkn.production_config import EuProductionSettings
from fck_rkn.production_firewall import FirewallInputError, prepare_eu_firewall_input


def settings(**overrides: object) -> EuProductionSettings:
    values = dict(
        EU_SERVER_ADDRESS="192.0.2.10",
        RU_SERVER_ADDRESS="198.51.100.20",
        SSH_USER="deploy",
        SSH_KEY_PATH=Path("/run/secrets/deploy_key"),
        SSH_PORT=2222,
        EU_METADATA_ADDRESS="192.0.2.1",
        EU_DNS_ADDRESSES=["192.0.2.53", "2001:db8::53"],
        EU_DHCP_SERVER_ADDRESS="192.0.2.2",
        EU_NTP_ADDRESSES=["192.0.2.123"],
        EU_LOCAL_NETWORKS=["192.0.2.0/24"],
    )
    values.update(overrides)
    return EuProductionSettings(**values)


def blacklist() -> tuple[list[str], list[str]]:
    return ["203.0.113.0/24"], ["2001:db8:dead::/48"]


class PrepareEuFirewallInputTests(unittest.TestCase):
    def test_builds_rules_input_from_production_settings_and_rkn_data(self) -> None:
        config = json.loads(prepare_eu_firewall_input(settings(), blacklist))

        self.assertEqual(config["ru_server_address"], "198.51.100.20")
        self.assertEqual(config["ssh_port"], 2222)
        self.assertEqual(config["rkn_ipv4"], ["203.0.113.0/24"])
        self.assertEqual(config["rkn_ipv6"], ["2001:db8:dead::/48"])
        self.assertEqual(
            config["vm_services"],
            {
                "metadata_address": "192.0.2.1",
                "dns_addresses": ["192.0.2.53", "2001:db8::53"],
                "dhcp_server_address": "192.0.2.2",
                "ntp_addresses": ["192.0.2.123"],
                "local_networks": ["192.0.2.0/24"],
            },
        )

    def test_result_is_accepted_by_the_eu_firewall_agent(self) -> None:
        rules = build_eu_firewall_rules(prepare_eu_firewall_input(settings(), blacklist))

        self.assertIn("tcp dport 2222 accept", rules)
        self.assertIn("198.51.100.20", rules)

    def test_contains_no_test_only_or_provider_specific_inputs(self) -> None:
        text = prepare_eu_firewall_input(settings(), blacklist)

        for forbidden in (
            "169.254.169.254",
            "crawler_source",
            "test_client",
            "provider_networks",
            "snapd",
            "github",
        ):
            self.assertNotIn(forbidden, text)

    def test_upstream_failures_raise_without_building_any_input(self) -> None:
        for label, error in (
            ("unreachable", RuntimeError("Could not fetch the required firewall update data")),
            ("invalid", ValueError("RKN ipv4 must contain networks")),
        ):
            with self.subTest(case=label):
                def fetch() -> tuple[list[str], list[str]]:
                    raise error

                with self.assertRaises(FirewallInputError):
                    prepare_eu_firewall_input(settings(), fetch)

    def test_empty_blacklist_is_rejected(self) -> None:
        for lists in (([], ["2001:db8:dead::/48"]), (["203.0.113.0/24"], [])):
            with self.subTest(lists=lists), self.assertRaises(FirewallInputError):
                prepare_eu_firewall_input(settings(), lambda: lists)

    def test_ru_peer_inside_a_crawler_range_is_rejected(self) -> None:
        for peer, lists in (
            ("203.0.113.5", blacklist()),
            ("2001:db8:dead::5", blacklist()),
        ):
            with self.subTest(peer=peer), self.assertRaises(FirewallInputError):
                prepare_eu_firewall_input(settings(RU_SERVER_ADDRESS=peer), lambda: lists)

    def test_ipv6_peer_outside_ranges_is_accepted(self) -> None:
        config = json.loads(
            prepare_eu_firewall_input(
                settings(RU_SERVER_ADDRESS="2001:db8:beef::5"), blacklist
            )
        )

        self.assertEqual(config["ru_server_address"], "2001:db8:beef::5")

    def test_module_does_not_import_the_gcp_orchestrator(self) -> None:
        source = Path(production_firewall.__file__).read_text(encoding="utf-8")

        self.assertNotIn("test_environment", source)
        self.assertNotIn("gcp", source.lower())


if __name__ == "__main__":
    unittest.main()
