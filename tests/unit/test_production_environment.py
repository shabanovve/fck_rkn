from __future__ import annotations

import re
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

from fck_rkn import production_setup
from fck_rkn.orchestration import production_environment
from fck_rkn.production_config import (
    EuProductionSettings,
    ProductionConfigError,
    load_eu_production_settings,
)
from fck_rkn.remote.ssh import SshRunner


def settings() -> EuProductionSettings:
    return EuProductionSettings(
        EU_SERVER_ADDRESS="192.0.2.10",
        RU_SERVER_ADDRESS="192.0.2.20",
        SSH_USER="deploy",
        SSH_KEY_PATH=Path("/run/secrets/deploy_key"),
        EU_METADATA_ADDRESS="192.0.2.1",
        EU_DNS_ADDRESSES=["192.0.2.53"],
        EU_DHCP_SERVER_ADDRESS="192.0.2.2",
        EU_NTP_ADDRESSES=["192.0.2.123"],
        EU_LOCAL_NETWORKS=["192.0.2.0/24"],
    )


class ProductionEnvironmentTests(unittest.TestCase):
    def test_creates_runner_only_for_eu_server(self) -> None:
        runner = production_environment.create_eu_runner(settings())

        self.assertEqual(
            runner,
            SshRunner(
                host="192.0.2.10",
                user="deploy",
                key_path=Path("/run/secrets/deploy_key"),
                port=22,
            ),
        )

    def test_deployment_runs_eu_operations_in_order(self) -> None:
        runner = object()
        with (
            patch.object(production_environment, "deploy_squid", return_value=0) as deploy,
            patch.object(production_environment, "check_squid_startup", return_value=0) as startup,
            patch.object(production_environment, "check_squid_http", return_value=0) as http,
            patch.object(production_environment, "check_squid_blocking", return_value=0) as blocking,
        ):
            self.assertEqual(production_environment.deploy_eu_squid(runner), 0)

        self.assertEqual(
            [action.call_args for action in (deploy, startup, http, blocking)],
            [call(runner)] * 4,
        )

    def test_deployment_stops_after_first_failed_operation(self) -> None:
        runner = object()
        with (
            patch.object(production_environment, "deploy_squid", return_value=0),
            patch.object(
                production_environment, "check_squid_startup", return_value=17
            ) as startup,
            patch.object(production_environment, "check_squid_http") as http,
            patch.object(production_environment, "check_squid_blocking") as blocking,
        ):
            self.assertEqual(production_environment.deploy_eu_squid(runner), 17)

        startup.assert_called_once_with(runner)
        http.assert_not_called()
        blocking.assert_not_called()

    def test_main_initializes_and_dispatches_eu_operation(self) -> None:
        with (
            patch.object(production_environment, "ensure_prod_config") as ensure,
            patch.object(
                production_environment,
                "load_eu_production_settings",
                return_value=settings(),
            ),
            patch.object(
                production_environment,
                "deploy_eu_squid",
                return_value=0,
            ) as deploy,
        ):
            self.assertEqual(production_environment.main(["deploy-eu-squid"]), 0)

        ensure.assert_called_once_with()
        self.assertIsInstance(deploy.call_args.args[0], SshRunner)
        self.assertEqual(deploy.call_args.args[0].host, "192.0.2.10")

    def test_main_without_arguments_deploys_eu_squid_with_the_eu_runner(self) -> None:
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=False),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                return_value=settings(),
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "deploy_eu_squid", return_value=0) as deploy,
        ):
            self.assertEqual(production_environment.main([]), 0)

        setup.assert_not_called()
        self.assertIsInstance(deploy.call_args.args[0], SshRunner)
        self.assertEqual(deploy.call_args.args[0].host, "192.0.2.10")

    def test_main_without_arguments_configures_first_when_file_is_missing(self) -> None:
        calls: list[str] = []
        with (
            patch.object(production_environment, "PROD_CONFIG_FILE", Path("/nonexistent/prod.env")),
            patch.object(production_environment.sys.stdin, "isatty", return_value=True),
            patch.object(
                production_environment,
                "configure_interactively",
                side_effect=lambda *a, **k: calls.append("configure"),
            ),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                return_value=settings(),
            ),
            patch.object(
                production_environment,
                "deploy_eu_squid",
                side_effect=lambda r: calls.append("deploy") or 0,
            ),
        ):
            self.assertEqual(production_environment.main([]), 0)

        self.assertEqual(calls, ["configure", "deploy"])

    def test_main_without_arguments_configures_when_file_is_invalid(self) -> None:
        error = ProductionConfigError("Invalid production configuration")
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=True),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                side_effect=[error, settings()],
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "deploy_eu_squid", return_value=0) as deploy,
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            self.assertEqual(production_environment.main([]), 0)

        setup.assert_called_once()
        deploy.assert_called_once()

    def test_main_without_terminal_and_invalid_config_exits_with_2(self) -> None:
        error = ProductionConfigError("Invalid production configuration")
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=False),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment, "load_eu_production_settings", side_effect=error
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "deploy_eu_squid") as deploy,
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            self.assertEqual(production_environment.main([]), 2)

        setup.assert_not_called()
        deploy.assert_not_called()

    def test_configure_operation_runs_interactive_setup_only(self) -> None:
        with (
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "deploy_eu_squid") as deploy,
        ):
            self.assertEqual(production_environment.main(["configure"]), 0)

        setup.assert_called_once_with(
            production_environment.create_runner,
            prompt=production_environment.read_input,
        )
        deploy.assert_not_called()

    def test_read_input_retries_after_invalid_utf8(self) -> None:
        bad = UnicodeDecodeError("utf-8", b"\xd0", 0, 1, "bad")
        with patch("builtins.input", side_effect=[bad, "n"]):
            self.assertEqual(production_environment.read_input("? "), "n")

    def test_configure_failure_returns_nonzero(self) -> None:
        error = production_environment.ProductionSetupError("declined")
        with (
            patch.object(
                production_environment, "configure_interactively", side_effect=error
            ),
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            self.assertEqual(production_environment.main(["configure"]), 2)

    def test_invalid_eu_address_in_real_file_stops_before_any_remote_operation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "prod.env"
            config.write_text(
                "EU_SERVER_ADDRESS=eu.example.com\n", encoding="utf-8"
            )
            with (
                patch.object(production_environment, "ensure_prod_config"),
                patch.object(
                    production_environment,
                    "load_eu_production_settings",
                    side_effect=lambda: load_eu_production_settings(config),
                ),
                patch.object(production_environment, "create_eu_runner") as create,
                patch.object(production_environment, "deploy_eu_squid") as deploy,
                patch.object(production_environment, "SshRunner") as ssh,
                self.assertLogs(production_environment.logger, level="ERROR") as logs,
            ):
                self.assertEqual(production_environment.main(["deploy-eu-squid"]), 2)

        create.assert_not_called()
        deploy.assert_not_called()
        ssh.assert_not_called()
        self.assertIn("EU_SERVER_ADDRESS", logs.output[0])

    def test_deployment_never_removes_the_eu_firewall(self) -> None:
        runner = object()
        with (
            patch.object(production_environment, "deploy_squid", return_value=0),
            patch.object(production_environment, "check_squid_startup", return_value=0),
            patch.object(production_environment, "check_squid_http", return_value=0),
            patch.object(production_environment, "check_squid_blocking", return_value=0),
            patch("fck_rkn.agents.eu_server.remove_eu_firewall") as remove,
            patch("fck_rkn.agents.eu_server.apply_eu_firewall") as apply,
        ):
            self.assertEqual(production_environment.deploy_eu_squid(runner), 0)

        remove.assert_not_called()
        apply.assert_not_called()

    def test_entry_point_does_not_reference_firewall_removal(self) -> None:
        for module in (production_environment, production_setup):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("remove_eu_firewall", source, module.__name__)
            self.assertNotIn("remove-firewall", source, module.__name__)

    def test_deployment_never_checks_firewall_absence(self) -> None:
        with (
            patch.object(production_environment, "deploy_squid", return_value=0),
            patch.object(production_environment, "check_squid_startup", return_value=0),
            patch.object(production_environment, "check_squid_http", return_value=0),
            patch.object(production_environment, "check_squid_blocking", return_value=0),
            patch(
                "fck_rkn.agents.eu_server_test.check_eu_firewall_removed"
            ) as absent,
        ):
            self.assertEqual(production_environment.deploy_eu_squid(object()), 0)

        absent.assert_not_called()
        for module in (production_environment, production_setup):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("check_eu_firewall_removed", source, module.__name__)

    def test_deployment_never_runs_the_icmp_ping_gate(self) -> None:
        with (
            patch.object(production_environment, "deploy_squid", return_value=0),
            patch.object(production_environment, "check_squid_startup", return_value=0),
            patch.object(production_environment, "check_squid_http", return_value=0),
            patch.object(production_environment, "check_squid_blocking", return_value=0),
            patch("fck_rkn.agents.eu_server.run_youtube_ping") as ping,
        ):
            self.assertEqual(production_environment.deploy_eu_squid(object()), 0)

        ping.assert_not_called()
        for module in (production_environment, production_setup):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("run_youtube_ping", source, module.__name__)

    def test_deployment_failure_stops_before_any_check_and_names_the_step(self) -> None:
        for label, code in (("remote failure", 5), ("timeout", 1)):
            with self.subTest(case=label):
                with (
                    patch.object(
                        production_environment, "deploy_squid", return_value=code
                    ),
                    patch.object(production_environment, "check_squid_startup") as startup,
                    patch.object(production_environment, "check_squid_http") as http,
                    patch.object(production_environment, "check_squid_blocking") as blocking,
                    self.assertLogs(production_environment.logger, level="ERROR") as logs,
                ):
                    self.assertEqual(production_environment.deploy_eu_squid(object()), code)

                startup.assert_not_called()
                http.assert_not_called()
                blocking.assert_not_called()
                self.assertIn("Squid deployment", logs.output[0])
                self.assertIn(str(code), logs.output[0])

    def test_deployment_uses_the_eu_runner_unchanged(self) -> None:
        runner = production_environment.create_eu_runner(settings())
        with (
            patch.object(production_environment, "deploy_squid", return_value=1) as deploy,
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            production_environment.deploy_eu_squid(runner)

        deploy.assert_called_once_with(runner)

    def test_entry_point_loads_no_gcp_code_or_credentials(self) -> None:
        code = (
            "import sys\n"
            "import fck_rkn.orchestration.production_environment\n"
            "bad = [m for m in sys.modules if m.split('.')[0] == 'google' "
            "or m.startswith(('fck_rkn.gcp', 'fck_rkn.orchestration.test_env'))]\n"
            "print(bad)\n"
            "raise SystemExit(1 if bad else 0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_entry_point_has_no_test_vm_or_network_provisioning(self) -> None:
        pattern = re.compile(
            r"ensure_eu_server|_provision_server|compute_v1|\bCloud NAT\b|"
            r"cloud_nat|ensure_.*firewall",
            re.IGNORECASE,
        )
        for module in (production_environment, production_setup):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(source), module.__name__)


if __name__ == "__main__":
    unittest.main()
