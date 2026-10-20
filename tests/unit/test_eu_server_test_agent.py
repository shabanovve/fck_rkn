from __future__ import annotations

import unittest
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
    OUTLINE_HEALTH_CHECK_COMMAND,
    SQUID_HTTP_CHECK_COMMAND,
    SQUID_STARTUP_CHECK_COMMAND,
    SSH_COMMAND_TIMEOUT_SECONDS,
    check_outline_health,
    check_squid_http,
    check_squid_startup,
    main,
)
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


if __name__ == "__main__":
    unittest.main()
