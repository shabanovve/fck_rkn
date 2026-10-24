from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fck_rkn import last_run
from fck_rkn.agents.eu_server_test import main as eu_main
from fck_rkn.agents.ru_server_test import main as ru_main


class LastRunStoreTests(unittest.TestCase):
    def test_saves_private_files_and_loads_them_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "last_run"
            last_run.save("a.json", '{"x": 1}', target)

            self.assertEqual(last_run.load("a.json", target), '{"x": 1}')
            self.assertEqual(stat.S_IMODE((target / "a.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_load_without_a_saved_run_explains_what_to_do(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "run test-env-host first"):
                last_run.load("missing.json", Path(directory))

    def test_save_failure_only_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "file"
            blocker.write_text("")
            with self.assertLogs("fck_rkn.last_run", level="WARNING"):
                last_run.save("a", "b", blocker / "sub")


class LastRunAgentTests(unittest.TestCase):
    def run_ru(self, action: str, saved: dict[str, str]):
        with tempfile.TemporaryDirectory() as directory:
            for name, content in saved.items():
                last_run.save(name, content, Path(directory))
            with (
                patch.object(last_run, "LAST_RUN_DIRECTORY", Path(directory)),
                patch.object(
                    last_run.load, "__defaults__", (Path(directory),)
                ),
                patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
                patch("fck_rkn.agents.ru_server_test.ru_server_runner") as runner,
                patch("fck_rkn.agents.ru_server_test.sys.stdin") as stdin,
                patch(
                    "fck_rkn.agents.ru_server_test.check_firewall_policy",
                    return_value=0,
                ) as policy,
                patch(
                    "fck_rkn.agents.ru_server_test.check_firewall_vpn_client",
                    return_value=0,
                ) as vpn,
                patch(
                    "fck_rkn.agents.ru_server_test.check_outline_client",
                    return_value=0,
                ) as client,
            ):
                result = ru_main([action, "--last-run"])
                stdin.read.assert_not_called()
                stdin.readline.assert_not_called()
            return result, runner.return_value, policy, vpn, client

    def test_ru_policy_check_uses_the_saved_firewall_config(self) -> None:
        result, runner, policy, _, _ = self.run_ru(
            "firewall-policy", {last_run.RU_FIREWALL_CONFIG: "{}"}
        )

        self.assertEqual(result, 0)
        policy.assert_called_once_with(runner, "{}")

    def test_ru_vpn_client_check_uses_the_saved_key_and_config(self) -> None:
        result, runner, _, vpn, _ = self.run_ru(
            "firewall-vpn-client",
            {last_run.TEST_CLIENT_KEY: "ss://k", last_run.RU_FIREWALL_CONFIG: "{}"},
        )

        self.assertEqual(result, 0)
        vpn.assert_called_once_with(runner, "ss://k", "{}")

    def test_ru_outline_client_check_uses_the_saved_probe_key(self) -> None:
        result, runner, _, _, client = self.run_ru(
            "outline-client", {last_run.RU_PROBE_KEY: "ss://p"}
        )

        self.assertEqual(result, 0)
        client.assert_called_once_with(runner, "ss://p")

    def test_missing_saved_input_fails_without_running_a_check(self) -> None:
        with self.assertLogs("fck_rkn.agents.ru_server_test", level="ERROR"):
            result, _, policy, _, _ = self.run_ru("firewall-policy", {})
        self.assertEqual(result, 2)
        policy.assert_not_called()

    def test_eu_firewall_check_uses_the_saved_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            last_run.save(last_run.EU_FIREWALL_CONFIG, json.dumps({"a": 1}), Path(directory))
            with (
                patch.object(last_run.load, "__defaults__", (Path(directory),)),
                patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
                patch("fck_rkn.agents.eu_server_test.eu_server_runner") as runner,
                patch(
                    "fck_rkn.agents.eu_server_test.check_eu_firewall",
                    return_value=0,
                ) as check,
            ):
                self.assertEqual(eu_main(["firewall", "--last-run"]), 0)
        check.assert_called_once_with(runner.return_value, '{"a": 1}')


if __name__ == "__main__":
    unittest.main()
