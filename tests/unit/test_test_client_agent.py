from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fake_runner import FakeRunner

from fck_rkn.agent_config import AgentSettings, test_client_runner
from fck_rkn.agents.test_client import (
    CONNECTION_CHECK_COMMAND,
    build_connection_deploy_command,
    check_outline_connection,
    connect_to_outline,
    main,
)


def make_access_url() -> str:
    userinfo = base64.urlsafe_b64encode(b"test-method:test-secret").decode()
    return f"ss://{userinfo}@configured-host:10001/?outline=1#name"


def make_settings(**overrides) -> AgentSettings:
    values = dict(
        EU_SERVER_HOST="eu",
        RU_SERVER_HOST="ru",
        EU_SERVER_PRIVATE_ADDRESS="eu-private",
        RU_SERVER_PRIVATE_ADDRESS="ru-private",
        SSH_USER="user",
        SSH_KEY_PATH=Path("key"),
        SSH_PORT=22,
    )
    values.update(overrides)
    return AgentSettings.model_construct(**values)


class TestClientAgentTests(unittest.TestCase):
    def test_connects_with_the_key_only_on_stdin(self) -> None:
        runner = FakeRunner(stdout="started\n")

        self.assertEqual(connect_to_outline(runner, make_access_url()), 0)

        self.assertNotIn("test-secret", runner.command)
        config = json.loads(runner.stdin)
        self.assertEqual(config["server"], "configured-host")
        self.assertEqual(config["server_port"], 10001)
        self.assertEqual(config["password"], "test-secret")
        self.assertEqual(config["local_address"], "127.0.0.1")

    def test_connection_script_is_private_idempotent_and_restarts(self) -> None:
        script = build_connection_deploy_command()
        self.assertIn("fck-rkn-test-client-outline", script)
        self.assertIn("ss-local", script)
        self.assertIn("--restart unless-stopped", script)
        self.assertIn("umask 077", script)
        self.assertIn("leaving it unchanged", script)
        self.assertNotIn("test-secret", script)

    def test_rejects_an_invalid_key_without_running_commands(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.test_client", "ERROR"):
            self.assertEqual(connect_to_outline(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])

    def test_remote_failure_does_not_leak_the_key(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", "ERROR") as logs:
            self.assertEqual(connect_to_outline(runner, make_access_url()), 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_check_verifies_the_connection_against_the_key_from_stdin(self) -> None:
        runner = FakeRunner(stdout="ok\n")

        self.assertEqual(check_outline_connection(runner, make_access_url()), 0)

        self.assertEqual(runner.command, CONNECTION_CHECK_COMMAND)
        self.assertNotIn("test-secret", runner.command)
        expected = json.loads(runner.stdin)
        self.assertEqual(expected["password"], "test-secret")
        self.assertEqual(expected["server"], "configured-host")

    def test_check_script_covers_container_key_listener_server_and_http(self) -> None:
        script = CONNECTION_CHECK_COMMAND
        self.assertIn("fck-rkn-test-client-outline", script)
        self.assertIn("/opt/fck-rkn-test-client-outline/config.json", script)
        self.assertIn("test key", script)
        self.assertIn("/dev/tcp/127.0.0.1/1080", script)
        self.assertIn("unreachable from the client", script)
        self.assertIn("--socks5-hostname 127.0.0.1:1080", script)
        self.assertNotIn("test-secret", script)

    def test_check_fails_without_leaking_the_key(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", "ERROR") as logs:
            self.assertEqual(check_outline_connection(runner, make_access_url()), 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_check_rejects_an_invalid_key_without_running_commands(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.test_client", "ERROR"):
            self.assertEqual(check_outline_connection(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])

    def test_cli_dispatches_the_check_action(self) -> None:
        with (
            patch("fck_rkn.agents.test_client.load_agent_settings"),
            patch("fck_rkn.agents.test_client.test_client_runner"),
            patch("fck_rkn.agents.test_client.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.test_client.check_outline_connection", return_value=0
            ) as check,
            patch("fck_rkn.agents.test_client.connect_to_outline") as connect,
        ):
            stdin.readline.return_value = "ss://key\n"
            self.assertEqual(main(["check-outline"]), 0)
        check.assert_called_once()
        connect.assert_not_called()

    def test_cli_reads_the_key_from_stdin(self) -> None:
        with (
            patch("fck_rkn.agents.test_client.load_agent_settings"),
            patch("fck_rkn.agents.test_client.test_client_runner"),
            patch("fck_rkn.agents.test_client.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.test_client.connect_to_outline", return_value=0
            ) as connect,
        ):
            stdin.readline.return_value = "ss://key\n"
            self.assertEqual(main(["connect-outline"]), 0)
        self.assertEqual(connect.call_args.args[1], "ss://key\n")

    def test_cli_fails_with_exit_2_on_invalid_configuration(self) -> None:
        with (
            patch(
                "fck_rkn.agents.test_client.load_agent_settings",
                side_effect=ValueError,
            ),
            self.assertLogs("fck_rkn.agents.test_client", "ERROR"),
        ):
            self.assertEqual(main(["connect-outline"]), 2)

    def test_runner_requires_a_configured_test_client_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "TEST_CLIENT_HOST"):
            test_client_runner(make_settings(TEST_CLIENT_HOST=""))
        runner = test_client_runner(make_settings(TEST_CLIENT_HOST="client"))
        self.assertEqual(runner.host, "client")


if __name__ == "__main__":
    unittest.main()
