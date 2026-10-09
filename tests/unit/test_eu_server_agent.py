from __future__ import annotations

import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fck_rkn.agents.eu_server import PING_COMMAND, run_youtube_ping
from fck_rkn.config import TestEnvironmentSettings


class EuServerAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )

    def test_runs_ping_on_eu_server_through_iap_and_returns_success(self) -> None:
        output = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="PING youtube.com: 1 packets transmitted, 1 received\n",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                return_value=completed,
            ) as run,
            redirect_stdout(output),
        ):
            result = run_youtube_ping(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(f"--command={PING_COMMAND}", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 90)
        self.assertEqual(result, 0)
        self.assertIn("1 packets transmitted", output.getvalue())

    def test_returns_nonzero_and_reports_failed_ping(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="PING youtube.com: 1 packets transmitted, 0 received\n",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                return_value=completed,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = run_youtube_ping(self.settings)

        self.assertEqual(result, 1)
        self.assertIn("0 received", stdout.getvalue())

    def test_returns_failure_if_gcloud_ssh_command_times_out(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=subprocess.TimeoutExpired("gcloud compute ssh", 90),
            ),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = run_youtube_ping(self.settings)

        self.assertNotEqual(result, 0)

    def test_returns_failure_if_gcloud_is_unavailable(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=FileNotFoundError("gcloud"),
            ),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = run_youtube_ping(self.settings)

        self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
