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
    SQUID_TUNNEL_CHECK_COMMAND,
    HTTP_REQUEST_COMMAND,
    check_squid_tunnel,
    request_youtube,
    build_connection_deploy_command,
    check_outline_connection,
    connect_to_outline,
    deploy_squid_tunnel,
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

    def test_squid_tunnel_targets_eu_squid_through_the_ru_key(self) -> None:
        runner = FakeRunner(stdout="started\n")

        result = deploy_squid_tunnel(runner, make_access_url(), "eu-squid.internal")

        self.assertEqual(result, 0)
        self.assertNotIn("test-secret", runner.command)
        self.assertIn("ss-tunnel", runner.command)
        self.assertIn("fck-rkn-test-client-squid", runner.command)
        config = json.loads(runner.stdin)
        self.assertEqual(config["server"], "configured-host")
        self.assertEqual(config["server_port"], 10001)
        self.assertEqual(config["password"], "test-secret")
        self.assertEqual(config["local_address"], "127.0.0.1")
        self.assertEqual(config["local_port"], 3128)
        self.assertEqual(config["tunnel_address"], "eu-squid.internal:3128")

    def test_squid_tunnel_uses_a_container_separate_from_the_connection(self) -> None:
        runner = FakeRunner()
        deploy_squid_tunnel(runner, make_access_url(), "eu-squid.internal")
        self.assertNotIn("fck-rkn-test-client-outline", runner.command)

    def test_squid_tunnel_rejects_bad_input_without_running_commands(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.test_client", "ERROR"):
            self.assertEqual(deploy_squid_tunnel(runner, "bad", "eu"), 1)
            self.assertEqual(
                deploy_squid_tunnel(runner, make_access_url(), " "), 1
            )
        self.assertEqual(runner.calls, [])

    def test_cli_passes_the_configured_eu_address_to_the_squid_tunnel(self) -> None:
        settings = make_settings(EU_SERVER_PRIVATE_ADDRESS="eu-private")
        with (
            patch("fck_rkn.agents.test_client.load_agent_settings", return_value=settings),
            patch("fck_rkn.agents.test_client.test_client_runner"),
            patch("fck_rkn.agents.test_client.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.test_client.deploy_squid_tunnel", return_value=0
            ) as deploy,
        ):
            stdin.readline.return_value = "ss://key\n"
            self.assertEqual(main(["connect-squid"]), 0)
        self.assertEqual(deploy.call_args.args[1:], ("ss://key\n", "eu-private"))

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


class SquidTunnelCheckTests(unittest.TestCase):
    def test_script_checks_container_and_localhost_listener(self) -> None:
        self.assertIn("fck-rkn-test-client-squid", SQUID_TUNNEL_CHECK_COMMAND)
        self.assertIn("/dev/tcp/127.0.0.1/3128", SQUID_TUNNEL_CHECK_COMMAND)
        self.assertIn("--proxy http://127.0.0.1:3128", SQUID_TUNNEL_CHECK_COMMAND)
        self.assertIn("did not reach EU Squid", SQUID_TUNNEL_CHECK_COMMAND)

    def test_check_runs_the_script_without_a_key(self) -> None:
        runner = FakeRunner(stdout="ok\n")
        self.assertEqual(check_squid_tunnel(runner), 0)
        self.assertEqual(runner.command, SQUID_TUNNEL_CHECK_COMMAND)
        self.assertEqual(runner.stdin, "")

    def test_check_fails_when_the_remote_check_fails(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", "ERROR"):
            self.assertEqual(check_squid_tunnel(FakeRunner(exit_code=1)), 1)

    def test_cli_check_squid_does_not_read_stdin(self) -> None:
        with (
            patch("fck_rkn.agents.test_client.load_agent_settings"),
            patch("fck_rkn.agents.test_client.test_client_runner"),
            patch("fck_rkn.agents.test_client.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.test_client.check_squid_tunnel", return_value=0
            ) as check,
        ):
            self.assertEqual(main(["check-squid"]), 0)
        check.assert_called_once()
        stdin.readline.assert_not_called()


class HttpRequestTests(unittest.TestCase):
    def test_script_requests_youtube_through_the_local_proxy(self) -> None:
        self.assertIn("--proxy http://127.0.0.1:3128", HTTP_REQUEST_COMMAND)
        self.assertIn("http://youtube.com/", HTTP_REQUEST_COMMAND)
        self.assertNotIn("--socks5", HTTP_REQUEST_COMMAND)

    def test_script_asserts_status_and_squid_origin(self) -> None:
        self.assertIn("2??|3??", HTTP_REQUEST_COMMAND)
        self.assertIn("Unexpected HTTP status", HTTP_REQUEST_COMMAND)
        self.assertIn("did not come from EU Squid", HTTP_REQUEST_COMMAND)
        self.assertIn("request through the proxy path failed", HTTP_REQUEST_COMMAND)

    def test_script_assertion_against_fake_proxy_responses(self) -> None:
        import subprocess
        import tempfile
        import os

        cases = (
            ("HTTP/1.1 301 Moved\r\nVia: 1.1 host (squid/6)\r\n\r\n", 0),
            ("HTTP/1.1 503 Down\r\nVia: 1.1 host (squid/6)\r\n\r\n", 1),
            ("HTTP/1.1 200 OK\r\nServer: nginx\r\n\r\n", 1),
        )
        for headers, expected in cases:
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as d:
                curl = Path(d, "curl")
                curl.write_text(f"#!/bin/sh\nprintf '%s' '{headers}'\n")
                curl.chmod(0o755)
                env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}
                result = subprocess.run(
                    ["bash", "-c", HTTP_REQUEST_COMMAND],
                    env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_request_runs_the_script_without_a_key(self) -> None:
        runner = FakeRunner(stdout="HTTP status through the proxy path: 200\n")
        self.assertEqual(request_youtube(runner), 0)
        self.assertEqual(runner.command, HTTP_REQUEST_COMMAND)
        self.assertEqual(runner.stdin, "")

    def test_request_fails_when_no_response_arrives(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", "ERROR"):
            self.assertEqual(request_youtube(FakeRunner(exit_code=1)), 1)

    def test_cli_dispatches_the_request_action(self) -> None:
        with (
            patch("fck_rkn.agents.test_client.load_agent_settings"),
            patch("fck_rkn.agents.test_client.test_client_runner"),
            patch("fck_rkn.agents.test_client.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.test_client.request_youtube", return_value=0
            ) as request,
        ):
            self.assertEqual(main(["request-youtube"]), 0)
        request.assert_called_once()
        stdin.readline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
