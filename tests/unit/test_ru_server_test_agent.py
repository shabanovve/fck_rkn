from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.ru_server_test import (
    CLIENT_CHECK_COMMAND,
    OUTLINE_HEALTH_CHECK_COMMAND,
    FORWARD_CHECK_COMMAND,
    TUNNEL_BLOCKED_CHECK_COMMAND,
    check_outline_client,
    check_outline_health,
    check_squid_forward,
    check_tunnel_blocked,
    main,
)


def make_access_url() -> str:
    userinfo = base64.urlsafe_b64encode(b"test-method:test-secret").decode()
    return f"ss://{userinfo}@configured-host:10001/?outline=1"


class RuServerClientCheckTests(unittest.TestCase):
    def test_verifies_client_against_test_key_passed_via_stdin(self) -> None:
        runner = FakeRunner(stdout="ok\n")
        result = check_outline_client(runner, make_access_url())

        self.assertEqual(result, 0)
        self.assertEqual(runner.command, CLIENT_CHECK_COMMAND)
        self.assertNotIn("test-secret", runner.command)
        expected = json.loads(runner.stdin)
        self.assertEqual(expected["password"], "test-secret")
        self.assertEqual(expected["server"], "configured-host")

    def test_check_script_covers_container_key_listener_and_server(self) -> None:
        self.assertIn("fck-rkn-outline-client", CLIENT_CHECK_COMMAND)
        self.assertIn("test key", CLIENT_CHECK_COMMAND)
        self.assertIn("/dev/tcp/127.0.0.1/1080", CLIENT_CHECK_COMMAND)
        self.assertIn("unreachable from the client", CLIENT_CHECK_COMMAND)
        self.assertIn("--socks5-hostname 127.0.0.1:1080", CLIENT_CHECK_COMMAND)
        self.assertIn("through the Outline client failed", CLIENT_CHECK_COMMAND)
        self.assertNotIn("test-secret", CLIENT_CHECK_COMMAND)

    def test_check_fails_when_remote_check_fails(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            result = check_outline_client(runner, make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_check_rejects_invalid_key_without_running_ssh(self) -> None:
        runner = FakeRunner()
        self.assertEqual(check_outline_client(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])


class RuServerForwardCheckTests(unittest.TestCase):
    def test_runs_http_check_without_secrets(self) -> None:
        runner = FakeRunner(stdout="ok\n")
        self.assertEqual(check_squid_forward(runner), 0)
        self.assertEqual(runner.command, FORWARD_CHECK_COMMAND)
        self.assertEqual(runner.stdin, "")

    def test_script_sends_http_through_forward_and_requires_squid(self) -> None:
        self.assertIn("fck-rkn-squid-forward", FORWARD_CHECK_COMMAND)
        self.assertIn("--proxy http://127.0.0.1:3128", FORWARD_CHECK_COMMAND)
        self.assertIn("squid", FORWARD_CHECK_COMMAND)
        self.assertIn("did not reach EU Squid", FORWARD_CHECK_COMMAND)

    def test_fails_when_remote_check_fails(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(check_squid_forward(FakeRunner(exit_code=1)), 1)


class RuServerTunnelBlockedTests(unittest.TestCase):
    def test_script_requires_stopped_tunnel_and_no_squid_response(self) -> None:
        script = TUNNEL_BLOCKED_CHECK_COMMAND
        self.assertIn("fck-rkn-squid-forward", script)
        self.assertIn("the tunnel is not lost", script)
        self.assertIn("--proxy http://127.0.0.1:3128", script)
        self.assertIn("Traffic still reaches EU Squid", script)
        self.assertNotIn("docker start", script)
        self.assertNotIn("docker restart", script)

    def test_runs_remote_check_without_secrets(self) -> None:
        runner = FakeRunner()
        self.assertEqual(check_tunnel_blocked(runner), 0)
        self.assertEqual(runner.stdin, "")
        self.assertEqual(runner.command, TUNNEL_BLOCKED_CHECK_COMMAND)

    def test_fails_when_traffic_is_not_blocked(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(check_tunnel_blocked(FakeRunner(exit_code=1)), 1)

    def test_cli_dispatches_tunnel_blocked(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server_test.check_tunnel_blocked", return_value=0) as check,
        ):
            self.assertEqual(main(["tunnel-blocked"]), 0)
        check.assert_called_once_with(make_runner.return_value)


class RuServerOutlineHealthTests(unittest.TestCase):
    def test_checks_container_authorized_api_and_access_port(self) -> None:
        runner = FakeRunner(stdout="healthy\n")
        self.assertEqual(check_outline_health(runner), 0)
        self.assertEqual(runner.command, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn("access-keys", runner.command)
        self.assertIn("/dev/tcp/127.0.0.1/10001", runner.command)

    def test_reports_remote_failure(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(check_outline_health(FakeRunner(exit_code=1)), 1)

    def test_cli_dispatches_outline_health(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server_test.check_outline_health", return_value=0) as check,
        ):
            self.assertEqual(main(["outline-health"]), 0)
        check.assert_called_once_with(make_runner.return_value)
