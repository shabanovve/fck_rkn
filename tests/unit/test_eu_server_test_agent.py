from __future__ import annotations

import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fck_rkn.agents.eu_server import SQUID_CONTAINER_NAME, SQUID_PORT
from fck_rkn.agents.eu_server_test import (
    SSH_COMMAND_TIMEOUT_SECONDS,
    SQUID_HTTP_CHECK_COMMAND,
    SQUID_STARTUP_CHECK_COMMAND,
    check_squid_http,
    check_squid_startup,
    main,
)
from fck_rkn.config import TestEnvironmentSettings


class EuServerTestAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )

    def test_checks_running_squid_container_and_tcp_listener_over_iap(self) -> None:
        output = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ) as run,
            redirect_stdout(output),
        ):
            result = check_squid_startup(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(
            f"--command={SQUID_STARTUP_CHECK_COMMAND}",
            command,
        )
        self.assertIn(SQUID_CONTAINER_NAME, SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn("Squid container is not running", SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn("Squid TCP listener is unavailable", SQUID_STARTUP_CHECK_COMMAND)
        self.assertIn(f"/dev/tcp/127.0.0.1/{SQUID_PORT}", SQUID_STARTUP_CHECK_COMMAND)
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            SSH_COMMAND_TIMEOUT_SECONDS,
        )
        self.assertEqual(result, 0)

    def test_returns_failure_when_container_or_listener_check_fails(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="container is not running\n",
        )
        stderr = StringIO()
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_squid_startup(self.settings)

        self.assertEqual(result, 1)
        self.assertIn("container is not running", stderr.getvalue())

    def test_returns_failure_when_check_times_out(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    "gcloud compute ssh",
                    SSH_COMMAND_TIMEOUT_SECONDS,
                ),
            ),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_squid_startup(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_loads_settings_and_runs_squid_startup_check(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.agents.eu_server_test.check_squid_startup",
                return_value=0,
            ) as check,
        ):
            result = main(["startup"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(self.settings)

    def test_checks_youtube_http_response_through_squid_over_iap(self) -> None:
        output = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="301\n",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ) as run,
            redirect_stdout(output),
        ):
            result = check_squid_http(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(f"--command={SQUID_HTTP_CHECK_COMMAND}", command)
        self.assertIn('HTTPConnection("127.0.0.1", 3128', SQUID_HTTP_CHECK_COMMAND)
        self.assertIn('request("GET", "http://youtube.com/"', SQUID_HTTP_CHECK_COMMAND)
        self.assertIn("200 <= status < 400", SQUID_HTTP_CHECK_COMMAND)
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            SSH_COMMAND_TIMEOUT_SECONDS,
        )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "301\n")

    def test_http_check_fails_for_http_error_response(self) -> None:
        stderr = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="503\n",
            stderr="Unexpected HTTP status: 503\n",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_squid_http(self.settings)

        self.assertEqual(result, 1)
        self.assertIn("Unexpected HTTP status: 503", stderr.getvalue())

    def test_http_check_fails_when_request_times_out(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    "gcloud compute ssh",
                    SSH_COMMAND_TIMEOUT_SECONDS,
                ),
            ),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_squid_http(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_http_check(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.agents.eu_server_test.check_squid_http",
                return_value=0,
            ) as check,
        ):
            result = main(["http"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(self.settings)


if __name__ == "__main__":
    unittest.main()
