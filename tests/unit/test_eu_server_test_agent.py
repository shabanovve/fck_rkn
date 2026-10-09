from __future__ import annotations

import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from fck_rkn.agents.eu_server import (
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_IMAGE,
    SQUID_CONTAINER_NAME,
    SQUID_PORT,
)
from fck_rkn.agents.eu_server_test import (
    OUTLINE_HEALTH_CHECK_COMMAND,
    OUTLINE_KEY_CHECK_COMMAND,
    OUTLINE_KEY_CLIENT_CONTAINER,
    OUTLINE_KEY_CLIENT_IMAGE,
    OUTLINE_KEY_LOCAL_SOCKS_PORT,
    OUTLINE_KEY_TEST_TIMEOUT_SECONDS,
    SSH_COMMAND_TIMEOUT_SECONDS,
    SQUID_HTTP_CHECK_COMMAND,
    SQUID_STARTUP_CHECK_COMMAND,
    check_outline_health,
    check_outline_access_key,
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

    def test_checks_outline_container_authorized_api_and_access_port(self) -> None:
        output = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Outline Server container, authorized API, and access endpoint are healthy\n",
            stderr="",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ) as run,
            redirect_stdout(output),
        ):
            result = check_outline_health(self.settings)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(f"--command={OUTLINE_HEALTH_CHECK_COMMAND}", command)
        self.assertIn(OUTLINE_CONTAINER_NAME, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn(OUTLINE_IMAGE, OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertIn(
            "access-keys",
            OUTLINE_HEALTH_CHECK_COMMAND,
        )
        self.assertIn(
            f"/dev/tcp/127.0.0.1/{OUTLINE_ACCESS_KEY_PORT}",
            OUTLINE_HEALTH_CHECK_COMMAND,
        )
        self.assertIn("json.load", OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertNotIn("print(keys", OUTLINE_HEALTH_CHECK_COMMAND)
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            SSH_COMMAND_TIMEOUT_SECONDS,
        )
        self.assertEqual(result, 0)
        self.assertIn("Outline Server", output.getvalue())

    def test_outline_health_check_reports_remote_failure(self) -> None:
        stderr = StringIO()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Outline Server authorized management endpoint is unhealthy\n",
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ),
            redirect_stderr(stderr),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_outline_health(self.settings)

        self.assertEqual(result, 1)
        self.assertIn("authorized management endpoint", stderr.getvalue())

    def test_outline_health_check_reports_ssh_timeout(self) -> None:
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
            result = check_outline_health(self.settings)

        self.assertNotEqual(result, 0)

    def test_cli_dispatches_outline_health_check(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.agents.eu_server_test.check_outline_health",
                return_value=0,
            ) as check,
        ):
            result = main(["outline"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(self.settings)

    def test_tests_outline_key_with_real_client_and_proxied_http_request(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "fck_rkn.agents.eu_server_test.subprocess.run",
            return_value=completed,
        ) as run:
            result = check_outline_access_key(self.settings, access_url)

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "eu-server"])
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn(f"--command={OUTLINE_KEY_CHECK_COMMAND}", command)
        self.assertNotIn(access_url, " ".join(command))
        self.assertEqual(run.call_args.kwargs["input"], access_url)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(
            run.call_args.kwargs["timeout"],
            OUTLINE_KEY_TEST_TIMEOUT_SECONDS,
        )
        self.assertIn(OUTLINE_KEY_CLIENT_IMAGE, OUTLINE_KEY_CHECK_COMMAND)
        self.assertIn(OUTLINE_KEY_CLIENT_CONTAINER, OUTLINE_KEY_CHECK_COMMAND)
        self.assertIn("sslocal -c /dev/stdin", OUTLINE_KEY_CHECK_COMMAND)
        self.assertIn("--socks5-hostname", OUTLINE_KEY_CHECK_COMMAND)
        self.assertIn("http://youtube.com/", OUTLINE_KEY_CHECK_COMMAND)
        self.assertIn(str(OUTLINE_KEY_LOCAL_SOCKS_PORT), OUTLINE_KEY_CHECK_COMMAND)
        self.assertEqual(result, 0)

    def test_outline_key_check_rejects_malformed_input(self) -> None:
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
            ) as run,
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR"),
        ):
            result = check_outline_access_key(self.settings, "not-an-outline-key")

        run.assert_not_called()
        self.assertEqual(result, 1)

    def test_outline_key_check_suppresses_remote_failure_output(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=access_url,
            stderr=access_url,
        )
        with (
            patch(
                "fck_rkn.agents.eu_server_test.subprocess.run",
                return_value=completed,
            ),
            self.assertLogs("fck_rkn.agents.eu_server_test", level="ERROR") as logs,
        ):
            result = check_outline_access_key(self.settings, access_url)

        self.assertEqual(result, 1)
        self.assertNotIn(access_url, "\n".join(logs.output))

    def test_cli_dispatches_outline_key_check_from_stdin(self) -> None:
        access_url = "ss://sensitive-test-key@example.invalid:10001/?outline=1"
        with (
            patch(
                "fck_rkn.agents.eu_server_test.load_config",
                return_value=self.settings,
            ),
            patch(
                "fck_rkn.agents.eu_server_test.sys.stdin",
                StringIO(access_url),
            ),
            patch(
                "fck_rkn.agents.eu_server_test.check_outline_access_key",
                return_value=0,
            ) as check,
        ):
            result = main(["outline-key"])

        self.assertEqual(result, 0)
        check.assert_called_once_with(self.settings, access_url)


if __name__ == "__main__":
    unittest.main()
