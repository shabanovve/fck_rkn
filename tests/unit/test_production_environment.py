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


def ru_settings() -> EuProductionSettings:
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
        RU_METADATA_ADDRESS="192.0.2.1",
        RU_DNS_ADDRESSES=["192.0.2.54"],
        RU_DHCP_SERVER_ADDRESS="192.0.2.3",
        RU_NTP_ADDRESSES=["192.0.2.124"],
        RU_LOCAL_NETWORKS=["192.0.3.0/24"],
        RU_PROVIDER_NETWORKS=["198.51.100.0/24"],
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

    def test_creates_runner_for_ru_server(self) -> None:
        runner = production_environment.create_ru_runner(ru_settings())

        self.assertEqual(
            runner,
            SshRunner(
                host="192.0.2.20",
                user="deploy",
                key_path=Path("/run/secrets/deploy_key"),
                port=22,
            ),
        )

    def test_create_ru_runner_fails_before_remote_work_when_settings_missing(
        self,
    ) -> None:
        with self.assertRaises(ProductionConfigError) as ctx:
            production_environment.create_ru_runner(settings())
        self.assertIn("RU_PROVIDER_NETWORKS", str(ctx.exception))
        self.assertIn("RU_DNS_ADDRESSES", str(ctx.exception))

    def test_load_ru_outline_image_loads_verified_artifact(self) -> None:
        runner = object()
        archive = Path("/tmp/verified.tar.gz")
        with (
            patch.object(
                production_environment,
                "retrieve_outline_artifact",
                return_value=archive,
            ) as retrieve,
            patch.object(
                production_environment,
                "load_outline_image",
                return_value=0,
            ) as load,
        ):
            self.assertEqual(production_environment.load_ru_outline_image(runner), 0)

        retrieve.assert_called_once_with()
        load.assert_called_once_with(runner, archive)

    def test_load_ru_outline_image_fails_when_artifact_unverified(self) -> None:
        runner = object()
        with (
            patch.object(
                production_environment,
                "retrieve_outline_artifact",
                return_value=None,
            ) as retrieve,
            patch.object(
                production_environment,
                "load_outline_image",
            ) as load,
        ):
            self.assertEqual(production_environment.load_ru_outline_image(runner), 1)

        retrieve.assert_called_once_with()
        load.assert_not_called()

    def test_ru_outline_deploy_steps_passes_ru_server_address(self) -> None:
        runner = object()
        settings = ru_settings()
        with patch.object(
            production_environment, "deploy_outline_server", return_value=0
        ) as deploy:
            steps = production_environment.ru_outline_deploy_steps(
                runner, settings.RU_SERVER_ADDRESS
            )
            self.assertEqual(len(steps), 1)
            name, action = steps[0]
            self.assertEqual(name, "RU Outline deployment")
            self.assertEqual(action(), 0)

        deploy.assert_called_once_with(runner, "192.0.2.20")

    def test_ru_outline_deploy_steps_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "deploy_outline_server", return_value=24
        ) as deploy:
            steps = production_environment.ru_outline_deploy_steps(runner, "192.0.2.20")
            _, action = steps[0]
            self.assertEqual(action(), 24)

    def test_ru_outline_steps_runs_deploy_and_health_check_in_order(self) -> None:
        runner = object()
        settings = ru_settings()
        with (
            patch.object(
                production_environment, "deploy_outline_server", return_value=0
            ) as deploy,
            patch.object(
                production_environment, "check_ru_outline_health", return_value=0
            ) as check,
        ):
            steps = production_environment.ru_outline_steps(
                runner, settings.RU_SERVER_ADDRESS
            )
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0][0], "RU Outline deployment")
            self.assertEqual(steps[1][0], "RU Outline health check")
            self.assertEqual(steps[0][1](), 0)
            self.assertEqual(steps[1][1](), 0)

        deploy.assert_called_once_with(runner, "192.0.2.20")
        check.assert_called_once_with(runner)

    def test_ru_outline_steps_health_check_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "check_ru_outline_health", return_value=25
        ) as check:
            steps = production_environment.ru_outline_steps(runner, "192.0.2.20")
            self.assertEqual(steps[1][1](), 25)

        check.assert_called_once_with(runner)

    def test_retrieve_ru_outline_key_returns_stored_key(self) -> None:
        store = object()
        with (
            patch.object(
                production_environment.LocalSecretStore,
                "read",
                return_value="ss://secret-key@192.0.2.10:10001",
            ),
        ):
            key = production_environment.retrieve_ru_outline_key()
            self.assertEqual(key, "ss://secret-key@192.0.2.10:10001")

    def test_retrieve_ru_outline_key_missing_or_error_fails_closed(self) -> None:
        with (
            patch.object(
                production_environment.LocalSecretStore,
                "read",
                return_value=None,
            ),
            self.assertLogs(production_environment.logger, level="ERROR") as logs,
        ):
            self.assertIsNone(production_environment.retrieve_ru_outline_key())
            self.assertIn("missing", logs.output[0])

        with (
            patch.object(
                production_environment.LocalSecretStore,
                "read",
                side_effect=production_environment.SecretStoreError("unsafe perms"),
            ),
            self.assertLogs(production_environment.logger, level="ERROR") as logs,
        ):
            self.assertIsNone(production_environment.retrieve_ru_outline_key())
            self.assertIn("unsafe perms", logs.output[0])
            self.assertNotIn("ss://", "\n".join(logs.output))

    def test_ru_outline_client_deploy_steps_passes_access_url(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with patch.object(
            production_environment, "deploy_outline_client", return_value=0
        ) as deploy:
            steps = production_environment.ru_outline_client_deploy_steps(
                runner, secret_url
            )
            self.assertEqual(len(steps), 1)
            name, action = steps[0]
            self.assertEqual(name, "RU Outline client deployment")
            self.assertEqual(action(), 0)

        deploy.assert_called_once_with(runner, secret_url)

    def test_ru_outline_client_deploy_steps_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "deploy_outline_client", return_value=16
        ) as deploy:
            steps = production_environment.ru_outline_client_deploy_steps(
                runner, "ss://key"
            )
            _, action = steps[0]
            self.assertEqual(action(), 16)

    def test_ru_outline_client_steps_runs_deploy_and_check_in_order(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with (
            patch.object(
                production_environment, "deploy_outline_client", return_value=0
            ) as deploy,
            patch.object(
                production_environment, "check_ru_outline_client", return_value=0
            ) as check,
        ):
            steps = production_environment.ru_outline_client_steps(runner, secret_url)
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0][0], "RU Outline client deployment")
            self.assertEqual(steps[1][0], "RU Outline client check")
            self.assertEqual(steps[0][1](), 0)
            self.assertEqual(steps[1][1](), 0)

        deploy.assert_called_once_with(runner, secret_url)
        check.assert_called_once_with(runner, secret_url)

    def test_ru_outline_client_steps_check_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "check_ru_outline_client", return_value=17
        ) as check:
            steps = production_environment.ru_outline_client_steps(runner, "ss://key")
            self.assertEqual(steps[1][1](), 17)

    def test_ru_squid_forward_deploy_steps_passes_access_url(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with patch.object(
            production_environment, "deploy_squid_forward", return_value=0
        ) as deploy:
            steps = production_environment.ru_squid_forward_deploy_steps(
                runner, secret_url
            )
            self.assertEqual(len(steps), 1)
            name, action = steps[0]
            self.assertEqual(name, "RU Squid forward deployment")
            self.assertEqual(action(), 0)

        deploy.assert_called_once_with(runner, secret_url)

    def test_ru_squid_forward_deploy_steps_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "deploy_squid_forward", return_value=18
        ) as deploy:
            steps = production_environment.ru_squid_forward_deploy_steps(
                runner, "ss://key"
            )
            _, action = steps[0]
            self.assertEqual(action(), 18)

    def test_ru_squid_forward_steps_runs_deploy_and_check_in_order(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with (
            patch.object(
                production_environment, "deploy_squid_forward", return_value=0
            ) as deploy,
            patch.object(
                production_environment, "check_ru_squid_forward", return_value=0
            ) as check,
        ):
            steps = production_environment.ru_squid_forward_steps(runner, secret_url)
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0][0], "RU Squid forward deployment")
            self.assertEqual(steps[1][0], "RU Squid forward check")
            self.assertEqual(steps[0][1](), 0)
            self.assertEqual(steps[1][1](), 0)

        deploy.assert_called_once_with(runner, secret_url)
        check.assert_called_once_with(runner)

    def test_ru_squid_forward_steps_check_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "check_ru_squid_forward", return_value=19
        ) as check:
            steps = production_environment.ru_squid_forward_steps(runner, "ss://key")
            self.assertEqual(steps[1][1](), 19)

    def test_ru_mtproxy_forward_deploy_steps_passes_access_url(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with patch.object(
            production_environment, "deploy_mtproxy_forward", return_value=0
        ) as deploy:
            steps = production_environment.ru_mtproxy_forward_deploy_steps(
                runner, secret_url
            )
            self.assertEqual(len(steps), 1)
            name, action = steps[0]
            self.assertEqual(name, "RU MTProxy forward deployment")
            self.assertEqual(action(), 0)

        deploy.assert_called_once_with(runner, secret_url)

    def test_ru_mtproxy_forward_deploy_steps_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "deploy_mtproxy_forward", return_value=38
        ) as deploy:
            steps = production_environment.ru_mtproxy_forward_deploy_steps(
                runner, "ss://key"
            )
            _, action = steps[0]
            self.assertEqual(action(), 38)

    def test_ru_mtproxy_forward_steps_runs_deploy_and_check_in_order(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with (
            patch.object(
                production_environment, "deploy_mtproxy_forward", return_value=0
            ) as deploy,
            patch.object(
                production_environment, "check_ru_mtproxy_forward", return_value=0
            ) as check,
        ):
            steps = production_environment.ru_mtproxy_forward_steps(
                runner, secret_url
            )
            self.assertEqual(len(steps), 2)
            self.assertEqual(steps[0][0], "RU MTProxy forward deployment")
            self.assertEqual(steps[1][0], "RU MTProxy forward check")
            self.assertEqual(steps[0][1](), 0)
            self.assertEqual(steps[1][1](), 0)

        deploy.assert_called_once_with(runner, secret_url)
        check.assert_called_once_with(runner)

    def test_ru_mtproxy_forward_steps_check_reports_failure(self) -> None:
        runner = object()
        with patch.object(
            production_environment, "check_ru_mtproxy_forward", return_value=39
        ) as check:
            steps = production_environment.ru_mtproxy_forward_steps(
                runner, "ss://key"
            )
            self.assertEqual(steps[1][1](), 39)


    def test_ru_firewall_steps_prepares_input_and_applies_firewall_in_order(self) -> None:
        runner = object()
        secret_url = "ss://Y2hhY2hhOnBhc3M@192.0.2.10:10001"
        with (
            patch(
                "fck_rkn.production_firewall.prepare_ru_firewall_input",
                return_value="prepared-ru-config",
            ) as prepare,
            patch.object(
                production_environment, "apply_ru_firewall", return_value=0
            ) as apply,
            patch.object(
                production_environment, "check_ru_firewall_policy_rules", return_value=0
            ) as check,
            patch.object(
                production_environment, "check_rkn_blacklist_updater", return_value=0
            ) as updater_check,
        ):
            steps = production_environment.ru_firewall_steps(
                runner, settings(), secret_url
            )
            self.assertEqual(len(steps), 4)
            self.assertEqual(steps[0][0], "RU firewall input preparation")
            self.assertEqual(steps[1][0], "RU firewall application")
            self.assertEqual(steps[2][0], "RU firewall verification")
            self.assertEqual(steps[3][0], "RU RKN blacklist updater check")
            self.assertEqual(steps[0][1](), 0)
            self.assertEqual(steps[1][1](), 0)
            self.assertEqual(steps[2][1](), 0)
            self.assertEqual(steps[3][1](), 0)

        prepare.assert_called_once_with(settings(), secret_url)
        apply.assert_called_once_with(runner, "prepared-ru-config")
        check.assert_called_once_with(runner, "prepared-ru-config")
        updater_check.assert_called_once_with(runner, "ru")

    def test_ru_firewall_steps_prepare_failure_stops_sequence(self) -> None:
        runner = object()
        error = production_environment.FirewallInputError("invalid ru input")
        with (
            patch(
                "fck_rkn.production_firewall.prepare_ru_firewall_input", side_effect=error
            ) as prepare,
            patch.object(production_environment, "apply_ru_firewall") as apply,
            patch.object(production_environment, "check_ru_firewall_policy_rules") as check,
            patch.object(production_environment, "check_rkn_blacklist_updater") as updater_check,
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            steps = production_environment.ru_firewall_steps(
                runner, settings(), "ss://key"
            )
            self.assertEqual(steps[0][1](), 1)
            
        prepare.assert_called_once()
        apply.assert_not_called()
        check.assert_not_called()
        updater_check.assert_not_called()

    def test_ru_firewall_steps_apply_reports_failure(self) -> None:
        runner = object()
        with (
            patch(
                "fck_rkn.production_firewall.prepare_ru_firewall_input",
                return_value="prepared-ru-config",
            ),
            patch.object(
                production_environment, "apply_ru_firewall", return_value=42
            ) as apply,
            patch.object(production_environment, "check_ru_firewall_policy_rules") as check,
            patch.object(production_environment, "check_rkn_blacklist_updater") as updater_check,
        ):
            steps = production_environment.ru_firewall_steps(
                runner, settings(), "ss://key"
            )
            steps[0][1]()
            self.assertEqual(steps[1][1](), 42)
        updater_check.assert_not_called()

    def test_ru_firewall_steps_check_reports_failure(self) -> None:
        runner = object()
        with (
            patch(
                "fck_rkn.production_firewall.prepare_ru_firewall_input",
                return_value="prepared-ru-config",
            ),
            patch.object(
                production_environment, "check_ru_firewall_policy_rules", return_value=43
            ) as check,
            patch.object(production_environment, "check_rkn_blacklist_updater") as updater_check,
        ):
            steps = production_environment.ru_firewall_steps(
                runner, settings(), "ss://key"
            )
            steps[0][1]()
            self.assertEqual(steps[2][1](), 43)
        updater_check.assert_not_called()

    def test_ru_firewall_steps_updater_check_reports_failure(self) -> None:
        runner = object()
        with (
            patch(
                "fck_rkn.production_firewall.prepare_ru_firewall_input",
                return_value="prepared-ru-config",
            ),
            patch.object(
                production_environment, "check_rkn_blacklist_updater", return_value=44
            ) as updater_check,
        ):
            steps = production_environment.ru_firewall_steps(
                runner, settings(), "ss://key"
            )
            steps[0][1]()
            self.assertEqual(steps[3][1](), 44)

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

    def test_main_without_arguments_runs_the_eu_stage_with_the_eu_runner(self) -> None:
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=False),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                return_value=ru_settings(),
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "run_eu_stage", return_value=0) as eu_deploy,
            patch.object(production_environment, "deploy_ru_stage", return_value=0) as ru_deploy,
        ):
            self.assertEqual(production_environment.main([]), 0)

        setup.assert_not_called()
        self.assertIsInstance(eu_deploy.call_args.args[0], SshRunner)
        self.assertEqual(eu_deploy.call_args.args[0].host, "192.0.2.10")
        self.assertIsInstance(ru_deploy.call_args.args[0], SshRunner)
        self.assertEqual(ru_deploy.call_args.args[0].host, "192.0.2.10")

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
                return_value=ru_settings(),
            ),
            patch.object(
                production_environment,
                "run_eu_stage",
                side_effect=lambda *a: calls.append("eu_deploy") and 0 or 0,
            ),
            patch.object(
                production_environment,
                "deploy_ru_stage",
                side_effect=lambda *a: calls.append("ru_deploy") and 0 or 0,
            ),
        ):
            self.assertEqual(production_environment.main([]), 0)

        self.assertEqual(calls, ["configure", "eu_deploy", "ru_deploy"])

    def test_main_without_arguments_configures_when_file_is_invalid(self) -> None:
        error = ProductionConfigError("Invalid production configuration")
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=True),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                side_effect=[error, ru_settings()],
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "run_eu_stage", return_value=0) as eu_deploy,
            patch.object(production_environment, "deploy_ru_stage", return_value=0) as ru_deploy,
            self.assertLogs(production_environment.logger, level="ERROR"),
        ):
            self.assertEqual(production_environment.main([]), 0)

        setup.assert_called_once()
        eu_deploy.assert_called_once()
        ru_deploy.assert_called_once()

    def test_main_without_terminal_and_invalid_config_exits_with_2(self) -> None:
        error = ProductionConfigError("Invalid production configuration")
        with (
            patch.object(production_environment.sys.stdin, "isatty", return_value=False),
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment, "load_eu_production_settings", side_effect=error
            ),
            patch.object(production_environment, "configure_interactively") as setup,
            patch.object(production_environment, "run_eu_stage") as deploy,
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

    def test_build_outline_artifact_operation_needs_no_configuration(self) -> None:
        with (
            patch.object(
                production_environment, "build_outline_artifact", return_value=0
            ) as build,
            patch.object(production_environment, "load_eu_production_settings") as load,
            patch.object(production_environment, "create_eu_runner") as create,
        ):
            self.assertEqual(
                production_environment.main(["build-outline-artifact"]), 0
            )

        build.assert_called_once_with()
        load.assert_not_called()
        create.assert_not_called()

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
