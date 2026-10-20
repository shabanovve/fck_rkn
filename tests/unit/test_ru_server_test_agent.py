from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.remote import render

from fck_rkn.agents.ru_server_test import (
    CLIENT_CHECK_COMMAND,
    OUTLINE_HEALTH_CHECK_COMMAND,
    FORWARD_CHECK_COMMAND,
    MTPROXY_FORWARD_CHECK_COMMAND,
    TUNNEL_BLOCKED_CHECK_COMMAND,
    check_outline_client,
    check_mtproxy_forward,
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


class MtproxyForwardCheckTests(unittest.TestCase):
    def test_check_runs_the_script_without_a_key(self) -> None:
        runner = FakeRunner(stdout="ok\n")
        self.assertEqual(check_mtproxy_forward(runner), 0)
        self.assertEqual(runner.command, MTPROXY_FORWARD_CHECK_COMMAND)
        self.assertEqual(runner.stdin, "")

    def test_check_fails_without_leaking_remote_output(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", "ERROR") as logs:
            result = check_mtproxy_forward(
                FakeRunner(exit_code=1, stdout="test-secret")
            )
        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_script_targets_the_mtproxy_forward_only(self) -> None:
        self.assertIn("fck-rkn-mtproxy-forward", MTPROXY_FORWARD_CHECK_COMMAND)
        self.assertIn("/dev/tcp/127.0.0.1/8443", MTPROXY_FORWARD_CHECK_COMMAND)
        self.assertNotIn("fck-rkn-squid-forward", MTPROXY_FORWARD_CHECK_COMMAND)
        self.assertNotIn("test-secret", MTPROXY_FORWARD_CHECK_COMMAND)

    def test_cli_dispatches_the_mtproxy_forward_check(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.ru_server_test.check_mtproxy_forward",
                return_value=0,
            ) as check,
        ):
            self.assertEqual(main(["mtproxy-forward"]), 0)
        check.assert_called_once_with(make_runner.return_value)

    def run_script(
        self,
        tunnel_to_mtproxy: bool = True,
        running: bool = True,
        outline_up: bool = True,
    ) -> subprocess.CompletedProcess:
        forward = socket.socket()
        forward.bind(("127.0.0.1", 0))
        forward.listen()
        outline = socket.socket()
        outline.bind(("127.0.0.1", 0))
        outline.listen()
        local_port = forward.getsockname()[1]
        outline_port = outline.getsockname()[1]
        if not outline_up:
            outline.close()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in (
                ("sudo", 'exec "$@"\n'),
                ("docker", f"echo {'running' if running else 'exited'}\n"),
            ):
                tool = root / name
                tool.write_text("#!/bin/sh\n" + body)
                tool.chmod(0o755)
            target = local_port if tunnel_to_mtproxy else local_port + 1
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "server": "127.0.0.1",
                        "server_port": outline_port,
                        "method": "m",
                        "password": "test-secret",
                        "local_address": "127.0.0.1",
                        "local_port": local_port,
                        "tunnel_address": f"127.0.0.1:{target}",
                    }
                )
            )
            script = render(
                "mtproxy_forward_check.sh",
                container_name="c",
                state_directory=str(root),
                local_port=local_port,
            )
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            try:
                return subprocess.run(
                    ["bash", "-c", script], env=env, capture_output=True, text=True
                )
            finally:
                forward.close()
                outline.close()

    def test_script_passes_for_a_forward_through_outline(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-secret", result.stdout + result.stderr)

    def test_script_fails_for_each_broken_condition(self) -> None:
        cases = (
            ({"running": False}, "not running"),
            ({"tunnel_to_mtproxy": False}, "not configured for the Outline path"),
            ({"outline_up": False}, "Outline server is unreachable"),
        )
        for options, message in cases:
            with self.subTest(message=message):
                result = self.run_script(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("test-secret", result.stdout + result.stderr)


class MtproxyForwardBlockedTests(unittest.TestCase):
    def test_check_runs_the_blocked_script_without_a_key(self) -> None:
        from fck_rkn.agents.ru_server_test import (
            MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND,
            check_mtproxy_forward_blocked,
        )

        runner = FakeRunner(stdout="blocked\n")
        self.assertEqual(check_mtproxy_forward_blocked(runner), 0)
        self.assertEqual(runner.command, MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND)
        self.assertIn("fck-rkn-mtproxy-forward", runner.command)
        self.assertEqual(runner.stdin, "")

    def test_script_fails_when_the_forward_still_listens(self) -> None:
        from fck_rkn.agents.ru_server_test import MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = MTPROXY_FORWARD_BLOCKED_CHECK_COMMAND.replace("8443", str(port))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in (("sudo", 'exec "$@"\n'), ("docker", "echo exited\n")):
                tool = root / name
                tool.write_text("#!/bin/sh\n" + body)
                tool.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            busy = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
            listener.close()
            free = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
        self.assertNotEqual(busy.returncode, 0)
        self.assertIn("still accepts", busy.stderr)
        self.assertEqual(free.returncode, 0, free.stderr)
