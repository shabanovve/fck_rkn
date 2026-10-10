from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fck_rkn.agents.eu_server import (
    PING_COMMAND,
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_API_PORT,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_DEPLOY_TIMEOUT_SECONDS,
    OUTLINE_IMAGE,
    OUTLINE_INSTALLER_URL,
    OUTLINE_STATE_DIRECTORY,
    SQUID_CONTAINER_NAME,
    SQUID_DEPLOY_TIMEOUT_SECONDS,
    SQUID_IMAGE,
    SQUID_PORT,
    deploy_squid,
    deploy_outline,
    create_outline_user,
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
        running_guard = (
            f'docker inspect --format=\'{{{{.State.Running}}}}\' '
            f"{SQUID_CONTAINER_NAME}"
        )
        self.assertIn(running_guard, remote_command)
        self.assertIn(
            "Squid container is already running; leaving it unchanged",
            remote_command,
        )
        self.assertLess(
            remote_command.index("leaving it unchanged"),
            remote_command.index(f"docker pull {SQUID_IMAGE}"),
        )
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

    def test_deploys_official_outline_server_with_private_endpoint(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch(
            "fck_rkn.agents.eu_server.subprocess.run",
            return_value=completed,
        ) as run:
            result = deploy_outline(self.settings)

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
        self.assertIn(OUTLINE_INSTALLER_URL, remote_command)
        self.assertIn(f"SB_IMAGE={OUTLINE_IMAGE}", remote_command)
        self.assertIn(f"CONTAINER_NAME={OUTLINE_CONTAINER_NAME}", remote_command)
        self.assertIn(f"SHADOWBOX_DIR={OUTLINE_STATE_DIRECTORY}", remote_command)
        self.assertIn("--hostname \"$private_ip\"", remote_command)
        self.assertIn(f"--api-port {OUTLINE_API_PORT}", remote_command)
        self.assertIn(f"--keys-port {OUTLINE_ACCESS_KEY_PORT}", remote_command)
        self.assertIn(">/dev/null 2>&1", remote_command)
        self.assertIn("Metadata-Flavor: Google", remote_command)
        self.assertIn("refusing to replace it", remote_command)
        self.assertIn(
            'if [ "$actual_image" = "$expected_image" ] && '
            '[ "$actual_state" = running ]; then',
            remote_command,
        )
        self.assertIn(
            "Outline Server container is already running; leaving it unchanged",
            remote_command,
        )
        self.assertLess(
            remote_command.index("leaving it unchanged"),
            remote_command.index(OUTLINE_INSTALLER_URL),
        )
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            OUTLINE_DEPLOY_TIMEOUT_SECONDS,
        )
        self.assertIs(run.call_args.kwargs["stdout"], sys.stdout)
        self.assertIs(run.call_args.kwargs["stderr"], sys.stderr)
        self.assertEqual(result, 0)

    def test_outline_deployment_reports_timeout(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    "gcloud compute ssh",
                    OUTLINE_DEPLOY_TIMEOUT_SECONDS,
                ),
            ),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            result = deploy_outline(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_deploy_outline_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_config", return_value=self.settings),
            patch(
                "fck_rkn.agents.eu_server.deploy_outline",
                return_value=0,
            ) as deploy,
        ):
            result = main(["deploy-outline"])

        self.assertEqual(result, 0)
        deploy.assert_called_once_with(self.settings)

    def test_creates_or_reuses_named_outline_user_and_returns_key_safely(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{access_url}\n",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                return_value=completed,
            ) as run,
            self.assertLogs("fck_rkn.agents.eu_server", level="INFO") as logs,
        ):
            result = create_outline_user(self.settings, "production-user")

        create_command = run.call_args.args[0]
        self.assertEqual(
            create_command[:4],
            ["gcloud", "compute", "ssh", "eu-server"],
        )
        remote_command = next(
            argument.removeprefix("--command=")
            for argument in create_command
            if argument.startswith("--command=")
        )
        self.assertIn("user_name = 'production-user'", remote_command)
        self.assertIn(
            "matching_keys = [key for key in keys if key.get('name') == user_name]",
            remote_command,
        )
        self.assertIn("method='POST'", remote_command)
        self.assertIn("json.load(response)", remote_command)
        self.assertIn("print(access_url)", remote_command)
        self.assertNotIn(access_url, remote_command)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(result, access_url)
        self.assertNotIn(access_url, "\n".join(logs.output))

    def test_outline_user_creation_failure_suppresses_secret_output(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=access_url,
            stderr=access_url,
        )
        with (
            patch(
                "fck_rkn.agents.eu_server.subprocess.run",
                return_value=completed,
            ) as run,
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR") as logs,
        ):
            result = create_outline_user(self.settings, "production-user")

        run.assert_called_once()
        self.assertIsNone(result)
        self.assertNotIn(access_url, "\n".join(logs.output))

    def test_cli_emits_key_only_for_explicit_outline_user_creation(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        output = StringIO()
        with (
            patch("fck_rkn.agents.eu_server.load_config", return_value=self.settings),
            patch(
                "fck_rkn.agents.eu_server.create_outline_user",
                return_value=access_url,
            ) as create,
            redirect_stdout(output),
        ):
            result = main(["create-outline-user", "--name", "production-user"])

        self.assertEqual(result, 0)
        create.assert_called_once_with(self.settings, "production-user")
        self.assertEqual(output.getvalue(), f"{access_url}\n")

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
