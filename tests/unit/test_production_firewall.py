from __future__ import annotations

import json
import unittest
from pathlib import Path

from fck_rkn import production_firewall
from fck_rkn.agents.eu_firewall import build_eu_firewall_rules
from fck_rkn.production_config import EuProductionSettings
from fck_rkn.production_firewall import (
    FirewallInputError,
    prepare_eu_firewall_input,
    prepare_ru_firewall_input,
)


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
        RU_METADATA_ADDRESS="198.51.100.1",
        RU_DNS_ADDRESSES=["198.51.100.53"],
        RU_DHCP_SERVER_ADDRESS="198.51.100.2",
        RU_NTP_ADDRESSES=["198.51.100.123"],
        RU_LOCAL_NETWORKS=["198.51.100.0/24"],
        RU_PROVIDER_NETWORKS=["203.0.113.128/25"],
    )
    values.update(overrides)
    return EuProductionSettings(**values)


def blacklist() -> tuple[list[str], list[str]]:
    return ["203.0.113.0/25"], ["2001:db8:dead::/48"]


class PrepareEuFirewallInputTests(unittest.TestCase):
    def test_builds_rules_input_from_production_settings_and_rkn_data(self) -> None:
        config = json.loads(prepare_eu_firewall_input(settings(), blacklist))

        self.assertEqual(config["ru_server_address"], "198.51.100.20")
        self.assertEqual(config["ssh_port"], 2222)
        self.assertEqual(config["rkn_ipv4"], ["203.0.113.0/25"])
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


class PrepareRuFirewallInputTests(unittest.TestCase):
    def test_builds_rules_input_from_settings_and_fetches(self) -> None:
        config = json.loads(
            prepare_ru_firewall_input(
                settings(),
                "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001",
                fetch_rkn=blacklist,
                fetch_github=lambda: ["198.51.100.128/25"],
                fetch_snapd=lambda: ["198.51.100.0/25"],
            )
        )

        self.assertEqual(config["eu_server_address"], "192.0.2.10")
        self.assertEqual(config["eu_outline_address"], "192.0.2.10")
        self.assertEqual(config["eu_outline_port"], 10001)
        self.assertEqual(config["ssh_port"], 2222)
        self.assertEqual(config["rkn_ipv4"], ["203.0.113.0/25"])
        self.assertEqual(config["rkn_ipv6"], ["2001:db8:dead::/48"])
        self.assertEqual(config["provider_networks"], ["203.0.113.128/25"])
        self.assertEqual(config["github_networks"], ["198.51.100.128/25"])
        self.assertEqual(config["snapd_networks"], ["198.51.100.0/25"])
        self.assertEqual(
            config["vm_services"],
            {
                "metadata_address": "198.51.100.1",
                "dns_addresses": ["198.51.100.53"],
                "dhcp_server_address": "198.51.100.2",
                "ntp_addresses": ["198.51.100.123"],
                "local_networks": ["198.51.100.0/24"],
            },
        )

    def test_missing_ru_settings_fail_early(self) -> None:
        with self.assertRaises(FirewallInputError) as ctx:
            prepare_ru_firewall_input(
                settings(RU_SERVER_ADDRESS=None, RU_DNS_ADDRESSES=None),
                "ss://key",
            )
        self.assertIn("RU_SERVER_ADDRESS", str(ctx.exception))
        self.assertIn("RU_DNS_ADDRESSES", str(ctx.exception))

    def test_invalid_access_url_fails_early(self) -> None:
        with self.assertRaises(FirewallInputError):
            prepare_ru_firewall_input(settings(), "invalid-url")

    def test_ru_server_inside_crawler_range_fails_early(self) -> None:
        with self.assertRaises(FirewallInputError):
            prepare_ru_firewall_input(
                settings(RU_SERVER_ADDRESS="203.0.113.5"),
                "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001",
                fetch_rkn=blacklist,
                fetch_github=lambda: [],
                fetch_snapd=lambda: [],
            )

    def test_fetch_failures_raise_firewall_input_error(self) -> None:
        for failing_arg in ("fetch_rkn", "fetch_github", "fetch_snapd"):
            with self.subTest(failing_arg=failing_arg):
                kwargs: dict = {
                    "fetch_rkn": blacklist,
                    "fetch_github": lambda: [],
                    "fetch_snapd": lambda: [],
                }
                kwargs[failing_arg] = lambda: (_ for _ in ()).throw(RuntimeError("x"))
                with self.assertRaises(FirewallInputError):
                    prepare_ru_firewall_input(
                        settings(), "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001", **kwargs
                    )

    def test_contains_no_test_only_inputs(self) -> None:
        text = prepare_ru_firewall_input(
            settings(),
            "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001",
            fetch_rkn=blacklist,
            fetch_github=lambda: ["198.51.100.128/25"],
            fetch_snapd=lambda: ["198.51.100.0/25"],
        )

        for forbidden in (
            "169.254.169.254",
            "crawler_source",
            "test_client",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
