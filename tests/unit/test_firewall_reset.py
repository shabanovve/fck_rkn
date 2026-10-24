from __future__ import annotations

import subprocess
import unittest

from fake_runner import FakeRunner

from fck_rkn.agents.eu_server import remove_eu_firewall
from fck_rkn.agents.eu_server_test import check_eu_firewall_removed
from fck_rkn.agents.firewall_common import (
    firewall_absent_check_command,
    firewall_blacklist_updater_check_command,
    firewall_blacklist_updater_install_command,
    firewall_remove_command,
)
from fck_rkn.agents.firewall_test_common import check_rkn_blacklist_updater
from fck_rkn.agents.ru_server import remove_ru_firewall
from fck_rkn.agents.ru_server_test import check_firewall_removed


class FirewallResetScriptTests(unittest.TestCase):
    def test_scripts_are_valid_bash_for_both_hosts(self) -> None:
        for host in ("eu", "ru"):
            for script in (
                firewall_remove_command(host),
                firewall_absent_check_command(host),
            ):
                with self.subTest(host=host):
                    result = subprocess.run(
                        ["bash", "-n"], input=script, capture_output=True, text=True
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_removal_deletes_table_unit_and_saved_rules_under_the_lock(self) -> None:
        script = firewall_remove_command("ru")

        self.assertIn("flock -w 60 /run/lock/fck-rkn-ru-firewall.lock", script)
        self.assertIn("systemctl disable --now fck-rkn-ru-firewall.service", script)
        self.assertIn("/etc/systemd/system/fck-rkn-ru-firewall.service", script)
        self.assertIn("/etc/fck-rkn/ru-firewall.nft", script)
        self.assertIn("/etc/systemd/system/fck-rkn-ru-blacklist-update.service", script)
        self.assertIn("/etc/systemd/system/fck-rkn-ru-blacklist-update.timer", script)
        self.assertIn("/usr/local/libexec/fck-rkn/rkn_blacklist.py", script)
        self.assertIn("systemctl disable --now fck-rkn-ru-blacklist-update.timer", script)
        self.assertIn("destroy table inet fck_rkn_ru", script)

    def test_absence_check_covers_table_files_and_service(self) -> None:
        script = firewall_absent_check_command("eu")

        self.assertIn("fck_rkn_eu", script)
        self.assertIn("/etc/fck-rkn/eu-firewall.nft", script)
        self.assertIn("fck-rkn-eu-firewall.service", script)
        self.assertIn("fck-rkn-eu-blacklist-update.timer", script)
        self.assertIn("/usr/local/libexec/fck-rkn/rkn_blacklist.py", script)
        self.assertEqual(script.count("PASS:"), 4)

    def test_shared_updater_scripts_target_each_host_and_use_common_logic(self) -> None:
        for host in ("eu", "ru"):
            installer = firewall_blacklist_updater_install_command(host)
            checker = firewall_blacklist_updater_check_command(host)
            with self.subTest(host=host):
                self.assertIn(
                    f"fck-rkn-{host}-blacklist-update.timer", installer
                )
                self.assertIn("OnCalendar=*-*-* 03:17:00", installer)
                self.assertIn("https://api.github.com/meta", installer)
                self.assertIn('"--check", "--file"', installer)
                self.assertIn(f"fck_rkn_{host}", checker)
                self.assertIn("rkn_ipv4 rkn_ipv6", checker)

    def test_shared_blacklist_test_helper_executes_the_rendered_check(self) -> None:
        runner = FakeRunner()

        self.assertEqual(check_rkn_blacklist_updater(runner, "eu"), 0)
        self.assertEqual(
            runner.command, firewall_blacklist_updater_check_command("eu")
        )
        self.assertIn("fck-rkn-eu-blacklist-update.timer", runner.command)


class FirewallResetAgentTests(unittest.TestCase):
    def test_agents_run_the_shared_scripts(self) -> None:
        for agent, host, runner_function in (
            (remove_eu_firewall, "eu", firewall_remove_command),
            (remove_ru_firewall, "ru", firewall_remove_command),
            (check_eu_firewall_removed, "eu", firewall_absent_check_command),
            (check_firewall_removed, "ru", firewall_absent_check_command),
        ):
            with self.subTest(agent=agent.__name__):
                runner = FakeRunner()

                self.assertEqual(agent(runner), 0)
                self.assertEqual(runner.command, runner_function(host))

    def test_agents_report_failures(self) -> None:
        for agent in (
            remove_eu_firewall,
            remove_ru_firewall,
            check_eu_firewall_removed,
            check_firewall_removed,
        ):
            with self.subTest(agent=agent.__name__):
                self.assertNotEqual(agent(FakeRunner(exit_code=1)), 0)


if __name__ == "__main__":
    unittest.main()
