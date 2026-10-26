from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.eu_server import (
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_IMAGE,
    SQUID_CONTAINER_NAME,
    SQUID_PORT,
)
from fck_rkn.agents.eu_server_test import (
    MTPROXY_CHECK_COMMAND,
    OUTLINE_HEALTH_CHECK_COMMAND,
    SQUID_BLOCK_CHECK_COMMAND,
    SQUID_HTTP_CHECK_COMMAND,
    SQUID_STARTUP_CHECK_COMMAND,
    SSH_COMMAND_TIMEOUT_SECONDS,
    check_eu_firewall_readonly,
    check_mtproxy,
    check_outline_health,
    check_squid_blocking,
    check_squid_http,
    check_squid_startup,
    main,
)
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteTimeoutError


class EuServerTestAgentTests(unittest.TestCase):
    def test_checks_running_squid_container_and_tcp_listener(self) -> None:
        runner = FakeRunner()
        result = check_squid_startup(runner)

        self.assertEqual(result, 0)
        self.assertEqual(
            runner.calls,
            [(SQUID_STARTUP_CHECK_COMMAND, None, SSH_COMMAND_TIMEOUT_SECONDS, False)],
        )
        self.assertIn(SQUID_CONTAINER_NAME, SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn("Squid container is not running", SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn("Squid TCP listener is unavailable", SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn(f"/dev/tcp/127.0.0.1/{SQUID_PORT}", SQUID_STARTUP_CHECK_COMMAND)

    def test_returns_failure_and_shows_diagnostics_when_check_fails(self) -> None:
        stderr = StringIO()
        runner = FakeRunner(exit_code=1, stderr="container is not running\n")
        with (
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.remote.runner", level="ERROR"),
        ):
            result = check_squid_startup(runner)

        self.assertEqual(result, 1)
        self.assertIn("container is not running", stderr.getvalue())

    def test_returns_failure_when_check_times_out(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = check_squid_startup(FakeRunner(error=RemoteTimeoutError()))

        self.assertNotEqual(result, 0)

    def test_cli_loads_settings_and_runs_squid_startup_check(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server_test.check_squid_startup",
                return_value=0,
            ) as check,
        ):
            result = main(["startup"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(make_runner.return_value)

    def test_cli_fails_with_exit_2_on_invalid_agent_configuration(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.load_agent_settings",
                side_effect=ValueError,
            ),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            self.assertEqual(main(["startup"]), 2)

    def test_checks_youtube_http_response_through_squid(self) -> None:
        output = StringIO()
        runner = FakeRunner(stdout="301\n")
        with redirect_stdout(output):
            result = check_squid_http(runner)

        self.assertEqual(result, 0)
        self.assertEqual(runner.command, SQUID_HTTP_CHECK_COMMAND)
        self.assertIn('HTTPConnection("127.0.0.1", int(sys.argv[1])', SQUID_HTTP_CHECK_COMMAND)
        self.assertIn("python3 - 3128", SQUID_HTTP_CHECK_COMMAND)
        self.assertIn('request("GET", "http://youtube.com/"', SQUID_HTTP_CHECK_COMMAND)
        self.assertIn("200 <= status < 400", SQUID_HTTP_CHECK_COMMAND)
        self.assertEqual(output.getvalue(), "301\n")

    def test_blocking_check_covers_ru_domains_and_ip_literals(self) -> None:
        runner = FakeRunner(stdout="GET example.ru: 403\n")
        with redirect_stdout(StringIO()):
            result = check_squid_blocking(runner)

        self.assertEqual(result, 0)
        self.assertEqual(runner.command, SQUID_BLOCK_CHECK_COMMAND)
        self.assertIn("python3 - 3128", SQUID_BLOCK_CHECK_COMMAND)
        for case in (
            '"example.ru"',
            '"example.xn--p1ai"',
            '("CONNECT", "example.ru"',
            '"192.0.2.1"',
            '"[2001:db8::1]"',
            '("CONNECT", "[2001:db8::1]"',
        ):
            self.assertIn(case, SQUID_BLOCK_CHECK_COMMAND)
        self.assertIn('status != "403"', SQUID_BLOCK_CHECK_COMMAND)

    def test_blocking_check_fails_when_a_request_is_not_denied(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="GET example.ru: 200\n")
        with (
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
            self.assertLogs("fck_rkn.remote.runner", level="ERROR"),
        ):
            self.assertEqual(check_squid_blocking(runner), 1)

    def test_http_check_fails_for_http_error_response(self) -> None:
        stderr = StringIO()
        runner = FakeRunner(
            exit_code=1, stdout="503\n", stderr="Unexpected HTTP status: 503\n"
        )
        with (
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.remote.runner", level="ERROR"),
        ):
            result = check_squid_http(runner)

        self.assertEqual(result, 1)
        self.assertIn("Unexpected HTTP status: 503", stderr.getvalue())

    def test_http_check_fails_when_request_times_out(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = check_squid_http(FakeRunner(error=RemoteTimeoutError()))

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_http_check(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server_test.check_squid_http",
                return_value=0,
            ) as check,
        ):
            result = main(["http"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(make_runner.return_value)

    def test_cli_dispatches_blacklist_updater_check(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server_test.check_rkn_blacklist_updater",
                return_value=0,
            ) as check,
        ):
            self.assertEqual(main(["firewall-blacklist"]), 0)
        check.assert_called_once_with(make_runner.return_value, "eu")

    def test_checks_outline_container_authorized_api_and_access_port(self) -> None:
        output = StringIO()
        runner = FakeRunner(stdout="Outline Server is healthy\n")
        with redirect_stdout(output):
            result = check_outline_health(runner)

        self.assertEqual(result, 0)
        self.assertEqual(runner.command, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn(OUTLINE_CONTAINER_NAME, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn(OUTLINE_IMAGE, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn("access-keys", OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn(
            f"/dev/tcp/127.0.0.1/{OUTLINE_ACCESS_KEY_PORT}",
            OUTLINE_HEALTH_CHECK_COMMAND,
        )
        self.assertIn("json.load", OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertNotIn("print(keys", OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn("Outline Server", output.getvalue())

    def test_outline_health_check_reports_remote_failure(self) -> None:
        stderr = StringIO()
        runner = FakeRunner(
            exit_code=1,
            stderr="Outline Server authorized management endpoint is unhealthy\n",
        )
        with (
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.remote.runner", level="ERROR"),
        ):
            result = check_outline_health(runner)

        self.assertEqual(result, 1)
        self.assertIn("authorized management endpoint", stderr.getvalue())

    def test_outline_health_check_reports_timeout(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            result = check_outline_health(FakeRunner(error=RemoteTimeoutError()))

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_outline_health_check(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server_test.check_outline_health",
                return_value=0,
            ) as check,
        ):
            result = main(["outline"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(make_runner.return_value)


SECRET = "0123456789abcdef0123456789abcdef"


class MtproxyCheckTests(unittest.TestCase):
    def test_check_runs_the_script_on_eu(self) -> None:
        runner = FakeRunner(stdout="ok\n")
        self.assertEqual(check_mtproxy(runner), 0)
        self.assertEqual(runner.command, MTPROXY_CHECK_COMMAND)
        self.assertEqual(runner.calls[0][2], SSH_COMMAND_TIMEOUT_SECONDS)

    def test_check_fails_without_leaking_remote_output(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", "ERROR") as logs:
            result = check_mtproxy(FakeRunner(exit_code=1, stdout="test-secret"))
        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_cli_dispatches_mtproxy_check(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.eu_server_test.check_mtproxy", return_value=0
            ) as check,
        ):
            self.assertEqual(main(["mtproxy"]), 0)
        check.assert_called_once_with(make_runner.return_value)

    def run_script(self, secret: str | None, mode: int, running: bool = True):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in (
                ("sudo", 'exec "$@"\n'),
                ("docker", f"echo {'true' if running else 'false'}\n"),
            ):
                tool = root / name
                tool.write_text("#!/bin/sh\n" + body)
                tool.chmod(0o755)
            if secret is not None:
                (root / "secret").write_text(secret)
                (root / "secret").chmod(mode)
            script = render(
                "mtproxy_check.sh",
                container_name="c",
                port=port,
                state_directory=str(root),
            )
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            try:
                return subprocess.run(
                    ["bash", "-c", script], env=env, capture_output=True, text=True
                )
            finally:
                listener.close()

    def test_script_passes_for_a_healthy_proxy_and_never_prints_the_secret(
        self,
    ) -> None:
        result = self.run_script(SECRET, 0o600)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_script_fails_for_each_broken_condition(self) -> None:
        cases = (
            (SECRET, 0o600, False, "not running"),
            (None, 0o600, True, "missing or malformed"),
            ("short", 0o600, True, "missing or malformed"),
            (SECRET, 0o644, True, "not root-only"),
        )
        for secret, mode, running, message in cases:
            with self.subTest(message=message, secret=secret, mode=mode):
                result = self.run_script(secret, mode, running)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn(SECRET, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()


READONLY_INPUT = '{"ru_server_address": "192.0.2.20", "ssh_port": 22}'
GOOD_RULES = """table inet fck_rkn_eu {
  set rkn_ipv4 {
    type ipv4_addr
    flags interval
    elements = { 203.0.113.0/24 }
  }
  set rkn_ipv6 {
    type ipv6_addr
    flags interval
    elements = { 2001:db8::/48 }
  }
  chain input {
    type filter hook input priority -5; policy drop;
    ip saddr @rkn_ipv4 counter limit rate 10/second burst 20 packets log prefix "fck-rkn eu input crawler-v4: " level info drop
    ip6 saddr @rkn_ipv6 counter limit rate 10/second burst 20 packets log prefix "fck-rkn eu input crawler-v6: " level info drop
    tcp dport 22 accept
    ip saddr 192.0.2.20 meta l4proto { tcp, udp } th dport 10001 accept
    counter drop
  }
  chain forward {
    type filter hook forward priority -5; policy accept;
    ct status dnat counter log prefix "fck-rkn eu forward direct-service: " drop
  }
}
"""


class FirewallReadonlyCheckTests(unittest.TestCase):
    def run_script(self, rules: str | None, stdin: str = READONLY_INPUT):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        log = root / "calls.log"
        (root / "rules.txt").write_text(rules or "", encoding="utf-8")
        sudo = root / "sudo"
        sudo.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        nft = root / "nft"
        nft.write_text(
            f'#!/bin/sh\necho "$@" >> {log}\n'
            f'[ -s {root}/rules.txt ] || exit 1\ncat {root}/rules.txt\n',
            encoding="utf-8",
        )
        for tool in (sudo, nft):
            tool.chmod(0o755)
        script = root / "check.sh"
        script.write_text(
            render("eu_firewall_readonly_check.sh", vpn_port=10001), encoding="utf-8"
        )
        result = subprocess.run(
            ["bash", str(script)],
            input=stdin,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}"},
        )
        calls = log.read_text() if log.exists() else ""
        return result, calls

    def test_matching_policy_passes_and_only_lists_the_table(self) -> None:
        result, calls = self.run_script(GOOD_RULES)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.strip(), "list table inet fck_rkn_eu")

    def test_mismatched_or_missing_policy_fails(self) -> None:
        cases = {
            "inactive": None,
            "accept policy": GOOD_RULES.replace("policy drop", "policy accept"),
            "no ssh": GOOD_RULES.replace("tcp dport 22 accept", "tcp dport 2222 accept"),
            "no ru allow": GOOD_RULES.replace("192.0.2.20", "192.0.2.99"),
            "empty set": GOOD_RULES.replace("elements = { 203.0.113.0/24 }", ""),
            "output hook": GOOD_RULES + "chain output { type filter hook output priority 0; }\n",
            "no crawler rule": GOOD_RULES.replace("crawler-v6", "other"),
            "no direct-service": GOOD_RULES.replace("direct-service", "x"),
        }
        for label, rules in cases.items():
            with self.subTest(case=label):
                result, _ = self.run_script(rules)

                self.assertNotEqual(result.returncode, 0)

    def test_script_has_no_mutating_or_probe_commands(self) -> None:
        script = render("eu_firewall_readonly_check.sh", vpn_port=10001)

        for forbidden in ("netns", "ip route", "ip link", "nft add", "nft -f",
                          "systemctl", "flush", "delete", "tee"):
            self.assertNotIn(forbidden, script)

    def test_agent_sends_only_non_secret_input_and_returns_the_exit_code(self) -> None:
        runner = FakeRunner(stdout="PASS\n")
        config = '{"ru_server_address": "192.0.2.20", "ssh_port": 22, "rkn_ipv4": ["1.2.3.0/24"]}'

        self.assertEqual(check_eu_firewall_readonly(runner, config), 0)

        self.assertEqual(
            runner.stdin, '{"ru_server_address": "192.0.2.20", "ssh_port": 22}'
        )
        self.assertEqual(runner.calls[0][2], SSH_COMMAND_TIMEOUT_SECONDS)

    def test_agent_reports_failure_timeout_and_invalid_input(self) -> None:
        with self.assertLogs(level="ERROR"):
            self.assertEqual(
                check_eu_firewall_readonly(FakeRunner(exit_code=1), READONLY_INPUT), 1
            )
        with self.assertLogs(level="ERROR"):
            self.assertEqual(
                check_eu_firewall_readonly(
                    FakeRunner(error=RemoteTimeoutError("slow")), READONLY_INPUT
                ),
                1,
            )
        runner = FakeRunner()
        with self.assertLogs(level="ERROR"):
            self.assertEqual(check_eu_firewall_readonly(runner, "{}"), 1)
        self.assertEqual(runner.calls, [])

    def test_cli_dispatches_firewall_readonly(self) -> None:
        with (
            patch("fck_rkn.agents.eu_server_test.load_agent_settings"),
            patch("fck_rkn.agents.eu_server_test.eu_server_runner"),
            patch("sys.stdin", StringIO(READONLY_INPUT)),
            patch(
                "fck_rkn.agents.eu_server_test.check_eu_firewall_readonly",
                return_value=0,
            ) as check,
        ):
            self.assertEqual(main(["firewall-readonly"]), 0)

        self.assertEqual(check.call_args.args[1], READONLY_INPUT)


class OutlineApiLoopbackFallbackTests(unittest.TestCase):
    def test_scripts_fall_back_to_loopback_when_the_configured_address_hangs(self) -> None:
        health = render("outline_health_check.sh", container_name="c", image="i",
                        state_directory="/s", keys_port=10001)
        user_script = (
            Path(__file__).parents[2]
            / "src/fck_rkn/remote/scripts/create_outline_user.py"
        ).read_text(encoding="utf-8")

        self.assertIn("--max-time 5", health)
        self.assertIn("127.0.0.1", health)
        self.assertIn("127.0.0.1", user_script)
        self.assertNotIn("--max-time", health.replace("--max-time 5", "").replace("--max-time 10", ""))
