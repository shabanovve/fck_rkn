from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fck_rkn.agents.eu_server import (
    PING_COMMAND,
    SQUID_CONTAINER_NAME,
    SQUID_DEPLOY_TIMEOUT_SECONDS,
    SQUID_IMAGE,
    SQUID_PORT,
    deploy_squid,
    follow_squid_logs,
    main,
    run_youtube_ping,
)
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

    def test_deploys_squid_container_over_iap_ssh(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
        )
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = deploy_squid(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        remote_command = next(
            argument.removeprefix("--command=")
            for argument in command
            if argument.startswith("--command=")
        )
        self.assertIn("apt-get install -y docker.io", remote_command)
        self.assertIn(f"docker pull {SQUID_IMAGE}", remote_command)
        self.assertIn(f"--name {SQUID_CONTAINER_NAME}", remote_command)
        self.assertIn("--restart unless-stopped", remote_command)
        self.assertIn(
            f"--publish {SQUID_PORT}:{SQUID_PORT}/tcp",
            remote_command,
        )
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            SQUID_DEPLOY_TIMEOUT_SECONDS,
        )
        self.assertIs(run.call_args.kwargs["stdout"], sys.stdout)
        self.assertIs(run.call_args.kwargs["stderr"], sys.stderr)
        self.assertEqual(result, 0)

    def test_squid_deployment_returns_failure_when_remote_command_fails(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Docker command failed\n",
        )
        stderr = StringIO()

        def fail_remote_command(*_args: object, **kwargs: object) -> subprocess.CompletedProcess:
            print("Docker command failed", file=kwargs["stderr"])
            return completed

        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=fail_remote_command,
            ),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = deploy_squid(self.settings)

        self.assertEqual(result, 1)
        self.assertIn("Docker command failed", stderr.getvalue())

    def test_squid_deployment_reports_timeout(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    "gcloud compute ssh",
                    SQUID_DEPLOY_TIMEOUT_SECONDS,
                ),
            ),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = deploy_squid(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_deploy_squid_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_config", return_value=self.settings),
            patch("fck_rkn.agents.eu_server.deploy_squid", return_value=0) as deploy,
        ):
            result = main(["deploy-squid"])

        self.assertEqual(result, 0)
        deploy.assert_called_once_with(self.settings)

    def test_cli_keeps_ping_as_default_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_config", return_value=self.settings),
            patch(
                "fck_rkn.agents.eu_server.run_youtube_ping",
                return_value=0,
            ) as ping,
        ):
            result = main([])

        self.assertEqual(result, 0)
        ping.assert_called_once_with(self.settings)

    def test_streams_squid_logs_live_over_iap(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch(
            "fck_rkn.agents.eu_server.subprocess.run",
            return_value=completed,
        ) as run:
            result = follow_squid_logs(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(
            f"--command=sudo docker logs --follow --timestamps "
            f"{SQUID_CONTAINER_NAME}",
            command,
        )
        self.assertIs(run.call_args.kwargs["stdout"], sys.stdout)
        self.assertIs(run.call_args.kwargs["stderr"], sys.stderr)
        self.assertNotIn("timeout", run.call_args.kwargs)
        self.assertEqual(result, 0)

    def test_reports_failure_if_squid_log_stream_cannot_start(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=FileNotFoundError("gcloud"),
            ),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = follow_squid_logs(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_squid_log_stream_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_config", return_value=self.settings),
            patch(
                "fck_rkn.agents.eu_server.follow_squid_logs",
                return_value=0,
            ) as follow,
        ):
            result = main(["logs-squid"])

        self.assertEqual(result, 0)
        follow.assert_called_once_with(self.settings)


if __name__ == "__main__":
    unittest.main()
