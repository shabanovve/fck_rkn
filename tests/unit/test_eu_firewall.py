from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.eu_firewall import build_eu_firewall_rules
from fck_rkn.agents.eu_server import apply_eu_firewall, main


def config(**overrides: object) -> str:
    data: dict[str, object] = {
        "ru_server_address": "10.0.0.24",
        "rkn_ipv4": ["192.0.2.0/24", "198.51.100.0/24"],
        "rkn_ipv6": ["2001:db8::/32"],
        "vm_services": {
            "metadata_address": "169.254.169.254",
            "dns_addresses": ["169.254.169.254"],
            "dhcp_server_address": "169.254.169.254",
            "ntp_addresses": ["169.254.169.254"],
            "local_networks": ["10.0.0.0/24"],
        },
    }
    data.update(overrides)
    return json.dumps(data)


def chain(rules: str, name: str) -> list[str]:
    start = rules.index(f"chain {name} {{")
    end = rules.index("\n  }", start)
    return [line.strip() for line in rules[start:end].splitlines()[1:]]


class EuFirewallRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = build_eu_firewall_rules(config())
        self.input = chain(self.rules, "input")[1:]

    def index(self, fragment: str) -> int:
        matches = [i for i, rule in enumerate(self.input) if fragment in rule]
        self.assertTrue(matches, fragment)
        return matches[0]

    def test_input_defaults_to_deny_and_output_is_not_filtered(self) -> None:
        self.assertIn("hook input priority -5; policy drop;", self.rules)
        self.assertNotIn("hook output", self.rules)

    def test_ssh_is_allowed_from_every_address(self) -> None:
        self.assertIn("tcp dport 22 accept", self.input)

    def test_custom_ssh_port(self) -> None:
        rules = build_eu_firewall_rules(config(ssh_port=2222))
        self.assertIn("tcp dport 2222 accept", chain(rules, "input"))

    def test_vpn_is_allowed_only_from_ru_server(self) -> None:
        self.assertIn(
            "ip saddr 10.0.0.24 meta l4proto { tcp, udp } th dport 10001 accept",
            self.input,
        )
        self.assertEqual(sum("10001" in rule for rule in self.input), 1)

    def test_crawlers_are_logged_and_denied_before_allowed_services(self) -> None:
        crawler = self.index("@rkn_ipv4")
        self.assertIn("log prefix", self.input[crawler])
        self.assertTrue(self.input[crawler].endswith("drop"))
        self.assertIn("2001:db8::/32", self.rules)
        self.assertLess(crawler, self.index("tcp dport 22"))
        self.assertLess(self.index("@rkn_ipv6"), self.index("tcp dport 22"))
        self.assertLess(crawler, self.index("th dport 10001"))

    def test_rule_order(self) -> None:
        self.assertEqual(self.input[0], 'iifname "lo" accept')
        self.assertEqual(self.input[1], "ct state established,related accept")
        self.assertLess(self.index("udp dport 68"), self.index("@rkn_ipv4"))
        self.assertLess(self.index("th dport 10001"), len(self.input) - 1)

    def test_everything_else_is_logged_and_denied_with_host_prefix(self) -> None:
        last = self.input[-1]
        self.assertIn('log prefix "fck-rkn eu input denied: "', last)
        self.assertTrue(last.endswith("drop"))
        self.assertIn("limit rate 10/second burst 20 packets", last)

    def test_minimal_vm_services_are_kept(self) -> None:
        self.assertIn("udp sport 67 udp dport 68 accept", " ".join(self.input))
        self.assertIn("icmp type", " ".join(self.input))
        self.assertIn("icmpv6 type", " ".join(self.input))

    def test_published_container_ports_are_denied_from_outside(self) -> None:
        forward = chain(self.rules, "forward")
        self.assertEqual(
            forward[0], "type filter hook forward priority -5; policy accept;"
        )
        self.assertIn("ct state established,related accept", forward)
        denial = [rule for rule in forward if "ct status dnat" in rule][0]
        self.assertIn("fck-rkn eu forward direct-service", denial)
        self.assertTrue(denial.endswith("drop"))

    def test_table_is_replaced_atomically_and_leaves_docker_chains(self) -> None:
        self.assertTrue(self.rules.startswith("destroy table inet fck_rkn_eu\n"))
        self.assertNotIn("flush ruleset", self.rules)

    def test_rejects_invalid_input(self) -> None:
        for bad in (
            "not json",
            "[]",
            config(ru_server_address="nope"),
            config(rkn_ipv4=[]),
            config(rkn_ipv6=["192.0.2.0/24"]),
            config(ssh_port=0),
            config(ssh_port=True),
            config(vm_services={}),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_eu_firewall_rules(bad)

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


class EuFirewallAgentTests(unittest.TestCase):
    def test_installs_rules_with_transactional_script(self) -> None:
        runner = FakeRunner(stdout="eu firewall rules installed\n")
        with redirect_stdout(StringIO()):
            self.assertEqual(apply_eu_firewall(runner, config()), 0)
        apply_command, apply_stdin = runner.calls[0][:2]
        updater_command = runner.calls[1][0]
        self.assertIn("nft --check --file", apply_command)
        self.assertIn("restore_previous", apply_command)
        self.assertIn("fck-rkn-eu-firewall.service", apply_command)
        self.assertIn("/etc/fck-rkn/eu-firewall.nft", apply_command)
        self.assertIn("destroy table inet fck_rkn_eu", apply_command)
        self.assertIn("destroy table inet fck_rkn_eu", apply_stdin)
        self.assertIn("Daily RKN crawler firewall blacklist refresh", updater_command)
        self.assertIn("systemctl start", updater_command)

    def test_invalid_configuration_runs_nothing(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"):
            self.assertEqual(apply_eu_firewall(runner, "{}"), 1)
        self.assertEqual(runner.calls, [])

    def test_cli_reads_configuration_from_stdin(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings"),
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch("fck_rkn.agents.eu_server.sys.stdin") as stdin,
            patch("fck_rkn.agents.eu_server.apply_eu_firewall", return_value=0) as apply,
        ):
            stdin.read.return_value = config()
            self.assertEqual(main(["apply-firewall"]), 0)
        apply.assert_called_once_with(make_runner.return_value, config())


if __name__ == "__main__":
    unittest.main()
