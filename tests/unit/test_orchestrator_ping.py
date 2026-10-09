from __future__ import annotations

import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

from google.cloud import compute_v1

from fck_rkn.config import TestEnvironmentSettings
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
        )

    def test_invokes_ping_only_after_project_and_vm_checks_pass(self) -> None:
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
        ):
            result = main()

        self.assertEqual(result, 0)
        load_config.assert_called_once_with()
        check_project.assert_called_once_with(self.settings)
        provision.assert_called_once_with(self.settings)
        ping.assert_called_once_with(self.settings)

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
            redirect_stderr(stderr),
        ):
            result = main()

        self.assertEqual(result, 6)
        ping.assert_called_once_with(self.settings)
        self.assertIn("ping check failed", stderr.getvalue())

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
        ):
            result = main()

        self.assertEqual(result, 5)
        ping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
