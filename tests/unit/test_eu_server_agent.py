from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.eu_server import (
    PING_COMMAND,
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_API_PORT,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_DEPLOY_TIMEOUT_SECONDS,
    OUTLINE_BASE_IMAGE,
    OUTLINE_IMAGE,
    OUTLINE_SS_SERVER_VERSION,
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
from fck_rkn.remote.runner import RemoteStartError, RemoteTimeoutError


class EuServerAgentTests(unittest.TestCase):
    def test_runs_ping_and_returns_success(self) -> None:
        output = StringIO()
        runner = FakeRunner(stdout="PING youtube.com: 1 packets transmitted\n")
        with redirect_stdout(output):
            result = run_youtube_ping(runner)

        self.assertEqual(result, 0)
        self.assertEqual(runner.calls, [(PING_COMMAND, None, 90, False)])
        self.assertIn("1 packets transmitted", output.getvalue())

    def test_returns_nonzero_and_reports_failed_ping(self) -> None:
        stdout = StringIO()
        runner = FakeRunner(exit_code=1, stdout="0 received\n")
        with (
            redirect_stdout(stdout),
            redirect_stderr(StringIO()),
            self.assertLogs("fck_rkn.remote.runner", level="ERROR"),
        ):
            result = run_youtube_ping(runner)

        self.assertEqual(result, 1)
        self.assertIn("0 received", stdout.getvalue())

    def test_returns_failure_if_transport_times_out_or_cannot_start(self) -> None:
        for error in (RemoteTimeoutError(), RemoteStartError("missing")):
            with self.subTest(error=type(error).__name__):
                with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
                    self.assertNotEqual(
                        run_youtube_ping(FakeRunner(error=error)), 0
                    )

    def test_deploys_squid_container(self) -> None:
        runner = FakeRunner()
        result = deploy_squid(runner)

        remote_command = runner.command
        self.assertIn("apt-get install -y docker.io", remote_command)
        self.assertIn(f"container_name={SQUID_CONTAINER_NAME}", remote_command)
        self.assertIn(
            "docker inspect --format='{{.State.Running}}' \"$container_name\"",
            remote_command,
        )
        self.assertIn(
            "Squid container is already running; leaving it unchanged",
            remote_command,
        )
        self.assertLess(
            remote_command.index("leaving it unchanged"),
            remote_command.index(f"docker pull {SQUID_IMAGE}"),
        )
        self.assertIn(f"docker pull {SQUID_IMAGE}", remote_command)
        self.assertIn('--name "$container_name"', remote_command)
        self.assertIn("--restart unless-stopped", remote_command)
        self.assertIn(
            f"--publish {SQUID_PORT}:{SQUID_PORT}/tcp",
            remote_command,
        )
        self.assertEqual(runner.calls[0][2], SQUID_DEPLOY_TIMEOUT_SECONDS)
        self.assertTrue(runner.calls[0][3])
        self.assertEqual(result, 0)

    def test_squid_deployment_returns_failure_when_remote_command_fails(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(deploy_squid(FakeRunner(exit_code=1)), 1)

    def test_squid_deployment_reports_timeout(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = deploy_squid(FakeRunner(error=RemoteTimeoutError()))

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_deploy_squid_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings"),
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch("fck_rkn.agents.eu_server.deploy_squid", return_value=0) as deploy,
        ):
            result = main(["deploy-squid"])

        self.assertEqual(result, 0)
        deploy.assert_called_once_with(make_runner.return_value)

    def test_cli_fails_with_exit_2_on_invalid_agent_configuration(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings", side_effect=ValueError),
            self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"),
        ):
            self.assertEqual(main(["deploy-squid"]), 2)

    def test_deploys_official_outline_server_with_private_endpoint(self) -> None:
        runner = FakeRunner()
        result = deploy_outline(runner, "eu.internal")

        remote_command = runner.command
        self.assertIn(OUTLINE_INSTALLER_URL, remote_command)
        self.assertIn(f"expected_image={OUTLINE_IMAGE}", remote_command)
        self.assertIn('SB_IMAGE="$expected_image"', remote_command)
        self.assertIn(f"container_name={OUTLINE_CONTAINER_NAME}", remote_command)
        self.assertIn('CONTAINER_NAME="$container_name"', remote_command)
        self.assertIn(f"SHADOWBOX_DIR={OUTLINE_STATE_DIRECTORY}", remote_command)
        self.assertIn("--hostname \"$private_address\"", remote_command)
        self.assertIn(f"--api-port {OUTLINE_API_PORT}", remote_command)
        self.assertIn(f"--keys-port {OUTLINE_ACCESS_KEY_PORT}", remote_command)
        self.assertIn(">/dev/null 2>&1", remote_command)
        self.assertIn(f"--branch {OUTLINE_SS_SERVER_VERSION}", remote_command)
        self.assertIn(f"FROM {OUTLINE_BASE_IMAGE}", remote_command)
        self.assertIn("Replacing the stock Outline Server container", remote_command)
        self.assertLess(
            remote_command.index("FCK_RKN_DOCKERFILE"),
            remote_command.index(OUTLINE_INSTALLER_URL),
        )
        self.assertIn("private_address=eu.internal", remote_command)
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
        self.assertEqual(runner.calls[0][2], OUTLINE_DEPLOY_TIMEOUT_SECONDS)
        self.assertTrue(runner.calls[0][3])
        self.assertEqual(result, 0)

    def test_outline_deployment_reports_timeout(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = deploy_outline(FakeRunner(error=RemoteTimeoutError()), "eu.internal")

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_deploy_outline_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings") as load,
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch("fck_rkn.agents.eu_server.deploy_outline", return_value=0) as deploy,
        ):
            result = main(["deploy-outline"])

        self.assertEqual(result, 0)
        deploy.assert_called_once_with(
            make_runner.return_value, load.return_value.EU_SERVER_PRIVATE_ADDRESS
        )

    def test_creates_or_reuses_named_outline_user_and_returns_key_safely(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        runner = FakeRunner(stdout=f"{access_url}\n")
        with self.assertLogs("fck_rkn.agents.eu_server", level="INFO") as logs:
            result = create_outline_user(runner, "production-user")

        remote_command = runner.command
        self.assertIn(
            "sudo python3 - production-user /opt/fck-rkn-outline",
            remote_command,
        )
        self.assertIn(
            "matching_keys = [key for key in keys if key.get('name') == user_name]",
            remote_command,
        )
        self.assertIn("method='POST'", remote_command)
        self.assertIn("json.load(response)", remote_command)
        self.assertIn("print(access_url)", remote_command)
        self.assertNotIn(access_url, remote_command)
        self.assertFalse(runner.calls[0][3])
        self.assertEqual(result, access_url)
        self.assertNotIn(access_url, "\n".join(logs.output))

    def test_outline_user_creation_failure_suppresses_secret_output(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        runner = FakeRunner(exit_code=1, stdout=access_url, stderr=access_url)
        with self.assertLogs("fck_rkn.agents.eu_server", level="ERROR") as logs:
            result = create_outline_user(runner, "production-user")

        self.assertEqual(len(runner.calls), 1)
        self.assertIsNone(result)
        self.assertNotIn(access_url, "\n".join(logs.output))

    def test_outline_user_creation_handles_transport_errors(self) -> None:
        for error in (RemoteTimeoutError(), RemoteStartError("missing")):
            with self.subTest(error=type(error).__name__):
                with self.assertLogs("fck_rkn.agents.eu_server", level="ERROR"):
                    self.assertIsNone(
                        create_outline_user(FakeRunner(error=error), "user")
                    )

    def test_cli_emits_key_only_for_explicit_outline_user_creation(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        output = StringIO()
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings"),
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server.create_outline_user",
                return_value=access_url,
            ) as create,
            redirect_stdout(output),
        ):
            result = main(["create-outline-user", "--name", "production-user"])

        self.assertEqual(result, 0)
        create.assert_called_once_with(make_runner.return_value, "production-user")
        self.assertEqual(output.getvalue(), f"{access_url}\n")

    def test_cli_keeps_ping_as_default_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings"),
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server.run_youtube_ping",
                return_value=0,
            ) as ping,
        ):
            result = main([])

        self.assertEqual(result, 0)
        ping.assert_called_once_with(make_runner.return_value)

    def test_streams_squid_logs_live_without_timeout(self) -> None:
        runner = FakeRunner()
        result = follow_squid_logs(runner)

        self.assertEqual(
            runner.calls,
            [
                (
                    f"sudo docker logs --follow --timestamps {SQUID_CONTAINER_NAME}",
                    None,
                    None,
                    True,
                )
            ],
        )
        self.assertEqual(result, 0)

    def test_reports_failure_if_squid_log_stream_cannot_start(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = follow_squid_logs(FakeRunner(error=RemoteStartError("x")))

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_squid_log_stream_action(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server.load_agent_settings"),
            patch("fck_rkn.agents.eu_server.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server.follow_squid_logs",
                return_value=0,
            ) as follow,
        ):
            result = main(["logs-squid"])

        self.assertEqual(result, 0)
        follow.assert_called_once_with(make_runner.return_value)


if __name__ == "__main__":
    unittest.main()
