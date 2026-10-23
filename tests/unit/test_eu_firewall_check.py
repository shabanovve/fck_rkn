from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.eu_server_test import (
    OUTBOUND_TEST_ADDRESS,
    build_firewall_check_command,
    build_firewall_check_input,
    check_eu_firewall,
    check_eu_firewall_rollback,
    main,
)
from test_eu_firewall import config


class EuFirewallCheckInputTests(unittest.TestCase):
    def test_derives_probe_addresses_from_the_firewall_configuration(self) -> None:
        result = json.loads(build_firewall_check_input(config()))

        self.assertEqual(
            result,
            {
                "ru_server_address": "10.0.0.24",
                "crawler_source_address": "192.0.2.0",
                "ssh_port": 22,
                "dns_address": "169.254.169.254",
                "vm_service_address": "169.254.169.254",
                "outbound_address": OUTBOUND_TEST_ADDRESS,
            },
        )

    def test_rejects_invalid_configuration(self) -> None:
        for bad in ("{}", "[]", config(rkn_ipv4=[]), config(ru_server_address="x")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_firewall_check_input(bad)


class EuFirewallCheckScriptTests(unittest.TestCase):
    def test_script_covers_every_required_flow(self) -> None:
        script = build_firewall_check_command()

        self.assertIn("vpn_port=10001", script)
        self.assertIn("proxy_ports=(3128 8443)", script)
        for text in (
            "SSH from an arbitrary address",
            "VPN port from ru-server",
            "VPN port from another host",
            "direct proxy port",
            "SSH from an RKN crawler range",
            "fck-rkn eu input crawler-v4:",
            "fck-rkn eu input denied:",
            "fck-rkn eu forward direct-service:",
            "Outgoing connection was blocked",
            "Name resolution failed",
        ):
            self.assertIn(text, script)

    def test_script_has_valid_bash_syntax(self) -> None:
        result = subprocess.run(
            ["bash", "-n"],
            input=build_firewall_check_command(),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck missing")
    def test_script_passes_shellcheck(self) -> None:
        result = subprocess.run(
            ["shellcheck", "-s", "bash", "-S", "error", "-"],
            input=build_firewall_check_command(),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout)


class EuFirewallCheckAgentTests(unittest.TestCase):
    def test_runs_the_script_with_derived_input_on_eu(self) -> None:
        runner = FakeRunner()

        self.assertEqual(check_eu_firewall(runner, config()), 0)

        self.assertEqual(runner.command, build_firewall_check_command())
        self.assertEqual(
            json.loads(runner.stdin), json.loads(build_firewall_check_input(config()))
        )

    def test_reports_failure_and_invalid_input(self) -> None:
        self.assertEqual(check_eu_firewall(FakeRunner(exit_code=1), config()), 1)
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"):
            self.assertEqual(check_eu_firewall(runner, "{}"), 1)
        self.assertEqual(runner.calls, [])

    def test_rollback_check_uses_the_shared_installer_for_eu(self) -> None:
        runner = FakeRunner()

        self.assertEqual(check_eu_firewall_rollback(runner), 0)

        for text in (
            "fck_rkn_eu",
            "fck-rkn-eu-firewall.service",
            "/etc/fck-rkn/eu-firewall.nft",
            "nftables rejected the firewall candidate",
        ):
            self.assertIn(text, runner.command)

    def test_cli_dispatches_firewall_checks(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch("fck_rkn.agents.eu_server_test.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.eu_server_test.check_eu_firewall", return_value=0
            ) as policy,
            patch(
                "fck_rkn.agents.eu_server_test.check_eu_firewall_rollback",
                return_value=0,
            ) as rollback,
        ):
            stdin.read.return_value = config()
            self.assertEqual(main(["firewall"]), 0)
            self.assertEqual(main(["firewall-rollback"]), 0)
        policy.assert_called_once_with(make_runner.return_value, config())
        rollback.assert_called_once_with(make_runner.return_value)


if __name__ == "__main__":
    unittest.main()
