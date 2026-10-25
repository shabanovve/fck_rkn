from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import call, patch

from fck_rkn.orchestration import production_environment
from fck_rkn.production_config import EuProductionSettings, ProductionConfigError
from fck_rkn.remote.ssh import SshRunner


def settings() -> EuProductionSettings:
    return EuProductionSettings(
        EU_SERVER_HOST="eu.example.net",
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
                host="eu.example.net",
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
        self.assertEqual(deploy.call_args.args[0].host, "eu.example.net")

    def test_main_defaults_to_eu_squid_deployment_without_arguments(self) -> None:
        with (
            patch.object(production_environment, "ensure_prod_config"),
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
            self.assertEqual(production_environment.main([]), 0)

        self.assertIsInstance(deploy.call_args.args[0], SshRunner)
        self.assertEqual(deploy.call_args.args[0].host, "eu.example.net")

    def test_main_returns_nonzero_if_configuration_is_invalid(self) -> None:
        with (
            patch.object(production_environment, "ensure_prod_config"),
            patch.object(
                production_environment,
                "load_eu_production_settings",
                side_effect=ProductionConfigError("invalid settings"),
            ),
            patch.object(production_environment, "deploy_eu_squid") as deploy,
        ):
            self.assertEqual(production_environment.main(["deploy-eu-squid"]), 2)

        deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
