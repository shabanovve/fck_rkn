from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import call, patch

from google.cloud import compute_v1

from fck_rkn.config import TestEnvironmentSettings
from fck_rkn.gcp.runner import GcloudIapRunner

EU_RUNNER = GcloudIapRunner("eu-server", "fck-rkn-test", "europe-north1-a")
from fck_rkn.orchestration.test_environment import main


class OrchestratorPingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )
        self.instance = compute_v1.Instance(
            name="eu-server",
            status="RUNNING",
            network_interfaces=[
                compute_v1.NetworkInterface(network_i_p="eu.internal")
            ],
        )
        self.outline_deploy = patch(
            "fck_rkn.orchestration.test_environment.deploy_outline",
            return_value=0,
        )
        self.outline_check = patch(
            "fck_rkn.orchestration.test_environment.check_outline_health",
            return_value=0,
        )
        self.outline_deploy_mock = self.outline_deploy.start()
        self.outline_check_mock = self.outline_check.start()
        self.addCleanup(self.outline_deploy.stop)
        self.addCleanup(self.outline_check.stop)
        self.ru_lookup = patch(
            "fck_rkn.orchestration.test_environment.ensure_ru_server",
            return_value=compute_v1.Instance(name="ru-server"),
        )
        self.ru_lookup_mock = self.ru_lookup.start()
        self.addCleanup(self.ru_lookup.stop)
        self.ru_key = patch(
            "fck_rkn.orchestration.test_environment.create_outline_user",
            return_value="ss://configured-key",
        )
        self.ru_client_deploy = patch(
            "fck_rkn.orchestration.test_environment.deploy_outline_client",
            return_value=0,
        )
        self.ru_client_check = patch(
            "fck_rkn.orchestration.test_environment.check_outline_client",
            return_value=0,
        )
        self.ru_forward_deploy = patch(
            "fck_rkn.orchestration.test_environment.deploy_squid_forward",
            return_value=0,
        )
        self.ru_forward_check = patch(
            "fck_rkn.orchestration.test_environment.check_squid_forward",
            return_value=0,
        )
        self.ru_tunnel_mocks = {}
        for name in (
            "stop_tunnel",
            "check_tunnel_blocked",
            "recover_tunnel",
        ):
            tunnel_patch = patch(
                f"fck_rkn.orchestration.test_environment.{name}",
                return_value=0,
            )
            self.ru_tunnel_mocks[name] = tunnel_patch.start()
            self.addCleanup(tunnel_patch.stop)
        self.ru_forward_deploy_mock = self.ru_forward_deploy.start()
        self.ru_forward_check_mock = self.ru_forward_check.start()
        self.addCleanup(self.ru_forward_deploy.stop)
        self.addCleanup(self.ru_forward_check.stop)
        self.ru_client_deploy_mock = self.ru_client_deploy.start()
        self.ru_client_check_mock = self.ru_client_check.start()
        self.addCleanup(self.ru_client_deploy.stop)
        self.addCleanup(self.ru_client_check.stop)
        self.ru_key_mock = self.ru_key.start()
        self.addCleanup(self.ru_key.stop)

    def test_invokes_ping_only_after_project_and_vm_checks_pass(self) -> None:
        output = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ) as load_config,
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ) as check_project,
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ) as provision,
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ) as ping,
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ) as deploy,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=0,
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http",
                return_value=0,
            ) as http_check,
            redirect_stdout(output),
            redirect_stderr(StringIO()),
        ):
            result = main()

        self.assertEqual(result, 0)
        load_config.assert_called_once_with()
        check_project.assert_called_once_with(self.settings)
        provision.assert_called_once_with(self.settings)
        self.ru_lookup_mock.assert_called_once_with(self.settings)
        ru_runner = GcloudIapRunner(
            "ru-server", "fck-rkn-test", "europe-north1-a"
        )
        self.ru_client_deploy_mock.assert_called_once_with(
            ru_runner, "ss://configured-key"
        )
        self.ru_client_check_mock.assert_called_once_with(
            ru_runner, "ss://configured-key"
        )
        self.ru_forward_deploy_mock.assert_called_once_with(
            ru_runner, "ss://configured-key"
        )
        self.assertEqual(
            self.ru_forward_check_mock.call_args_list,
            [call(ru_runner), call(ru_runner)],
        )
        for mock in self.ru_tunnel_mocks.values():
            mock.assert_called_once_with(ru_runner)
        ping.assert_called_once_with(EU_RUNNER)
        deploy.assert_called_once_with(EU_RUNNER)
        squid_check.assert_called_once_with(EU_RUNNER)
        http_check.assert_called_once_with(EU_RUNNER)
        self.outline_deploy_mock.assert_called_once_with(
            EU_RUNNER, "eu.internal"
        )
        self.outline_check_mock.assert_called_once_with(EU_RUNNER)

    def test_fails_workflow_when_ping_agent_returns_nonzero(self) -> None:
        stderr = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=1,
            ) as ping,
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid"
            ) as deploy,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup"
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http"
            ) as http_check,
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 6)
        ping.assert_called_once_with(EU_RUNNER)
        deploy.assert_not_called()
        squid_check.assert_not_called()
        http_check.assert_not_called()
        self.assertIn("ping check failed", stderr.getvalue())

    def test_fails_if_squid_deployment_fails_and_skips_startup_check(self) -> None:
        stderr = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=1,
            ) as deploy,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup"
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http"
            ) as http_check,
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 7)
        deploy.assert_called_once_with(EU_RUNNER)
        squid_check.assert_not_called()
        http_check.assert_not_called()
        self.assertIn("Squid deployment failed", stderr.getvalue())

    def test_fails_workflow_when_squid_startup_check_fails(self) -> None:
        stderr = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=1,
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http"
            ) as http_check,
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 8)
        squid_check.assert_called_once_with(EU_RUNNER)
        http_check.assert_not_called()
        self.assertIn("Squid startup check failed", stderr.getvalue())

    def test_fails_workflow_when_squid_http_check_fails(self) -> None:
        stderr = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=0,
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http",
                return_value=1,
            ) as http_check,
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 9)
        squid_check.assert_called_once_with(EU_RUNNER)
        http_check.assert_called_once_with(EU_RUNNER)
        self.assertIn("Squid HTTP check failed", stderr.getvalue())

    def test_fails_if_outline_deployment_fails_and_skips_health_check(self) -> None:
        stderr = StringIO()
        self.outline_deploy_mock.return_value = 1
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http",
                return_value=0,
            ),
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 10)
        self.outline_deploy_mock.assert_called_once_with(
            EU_RUNNER, "eu.internal"
        )
        self.outline_check_mock.assert_not_called()
        self.assertIn("Outline deployment failed", stderr.getvalue())

    def test_fails_workflow_when_outline_health_check_fails(self) -> None:
        stderr = StringIO()
        self.outline_check_mock.return_value = 1
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http",
                return_value=0,
            ),
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 11)
        self.outline_deploy_mock.assert_called_once_with(
            EU_RUNNER, "eu.internal"
        )
        self.outline_check_mock.assert_called_once_with(EU_RUNNER)
        self.assertIn("Outline health check failed", stderr.getvalue())

    def run_main_with_healthy_eu(self) -> tuple[int, StringIO]:
        stderr = StringIO()
        with (
            patch("fck_rkn.orchestration.test_environment.logging.basicConfig"),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch("fck_rkn.orchestration.test_environment.validate_project_access"),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                return_value=self.instance,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup",
                return_value=0,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http",
                return_value=0,
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
        ):
            return main(), stderr

    def test_fails_when_ru_outline_key_cannot_be_created(self) -> None:
        self.ru_key_mock.return_value = None

        result, stderr = self.run_main_with_healthy_eu()

        self.assertEqual(result, 13)
        self.assertIn("Outline access key", stderr.getvalue())

    def test_stops_when_ru_outline_client_deployment_fails(self) -> None:
        self.ru_client_deploy_mock.return_value = 1

        result, stderr = self.run_main_with_healthy_eu()

        self.assertEqual(result, 16)
        self.ru_client_check_mock.assert_not_called()
        self.assertIn("client deployment failed", stderr.getvalue())
        self.assertNotIn("ss://configured-key", stderr.getvalue())

    def test_fails_when_ru_outline_client_check_fails(self) -> None:
        self.ru_client_check_mock.return_value = 1

        result, stderr = self.run_main_with_healthy_eu()

        self.assertEqual(result, 17)
        self.assertIn("client check failed", stderr.getvalue())

    def test_stops_when_ru_squid_forward_deployment_fails(self) -> None:
        self.ru_forward_deploy_mock.return_value = 1

        result, stderr = self.run_main_with_healthy_eu()

        self.assertEqual(result, 18)
        self.ru_forward_check_mock.assert_not_called()
        self.assertIn("forward deployment failed", stderr.getvalue())
        self.assertNotIn("ss://configured-key", stderr.getvalue())

    def test_fails_when_ru_squid_forward_check_fails(self) -> None:
        self.ru_forward_check_mock.return_value = 1

        result, stderr = self.run_main_with_healthy_eu()

        self.assertEqual(result, 19)
        self.assertIn("forward check failed", stderr.getvalue())
        self.ru_tunnel_mocks["stop_tunnel"].assert_not_called()

    def test_tunnel_loss_steps_run_in_order_and_fail_with_own_codes(self) -> None:
        for name, code in (
            ("stop_tunnel", 20),
            ("check_tunnel_blocked", 21),
            ("recover_tunnel", 22),
        ):
            with self.subTest(step=name):
                for mock in self.ru_tunnel_mocks.values():
                    mock.reset_mock(return_value=False)
                    mock.return_value = 0
                self.ru_tunnel_mocks[name].return_value = 1

                result, stderr = self.run_main_with_healthy_eu()

                self.assertEqual(result, code)
                self.assertIn("failed with exit status 1", stderr.getvalue())

    def test_fails_when_forward_does_not_resume_after_recovery(self) -> None:
        self.ru_forward_check_mock.side_effect = [0, 1]

        result, _ = self.run_main_with_healthy_eu()

        self.assertEqual(result, 23)

    def test_recovery_is_not_attempted_when_traffic_is_not_blocked(self) -> None:
        self.ru_tunnel_mocks["check_tunnel_blocked"].return_value = 1

        self.run_main_with_healthy_eu()

        self.ru_tunnel_mocks["recover_tunnel"].assert_not_called()

    def test_forward_is_not_deployed_when_client_check_fails(self) -> None:
        self.ru_client_check_mock.return_value = 1

        result, _ = self.run_main_with_healthy_eu()

        self.assertEqual(result, 17)
        self.ru_forward_deploy_mock.assert_not_called()
        self.ru_forward_check_mock.assert_not_called()

    def test_does_not_run_ping_if_vm_provisioning_fails(self) -> None:
        with (
            patch(
                "fck_rkn.orchestration.test_environment.logging.basicConfig"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.orchestration.test_environment.validate_project_access"
            ),
            patch(
                "fck_rkn.orchestration.test_environment.ensure_eu_server",
                side_effect=RuntimeError("provisioning failed"),
            ),
            patch(
                "fck_rkn.orchestration.test_environment.run_youtube_ping"
            ) as ping,
            patch(
                "fck_rkn.orchestration.test_environment.deploy_squid"
            ) as deploy,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_startup"
            ) as squid_check,
            patch(
                "fck_rkn.orchestration.test_environment.check_squid_http"
            ) as http_check,
        ):
            result = main()

        self.assertEqual(result, 5)
        ping.assert_not_called()
        deploy.assert_not_called()
        squid_check.assert_not_called()
        http_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
