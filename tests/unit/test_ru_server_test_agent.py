from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

import json

from fck_rkn.agents.ru_server_test import (
    CLIENT_CHECK_COMMAND,
    FORWARD_CHECK_COMMAND,
    check_outline_client,
    check_squid_forward,
)
from fck_rkn.config import TestEnvironmentSettings


def make_access_url() -> str:
    userinfo = base64.urlsafe_b64encode(b"test-method:test-secret").decode()
    return f"ss://{userinfo}@configured-host:10001/?outline=1"


class RuServerClientCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )

    def test_verifies_client_against_test_key_passed_via_stdin(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = check_outline_client(self.settings, make_access_url())

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertNotIn("test-secret", " ".join(command))
        expected = json.loads(run.call_args.kwargs["input"])
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
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="test-secret"
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR") as logs,
        ):
            result = check_outline_client(self.settings, make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_check_rejects_invalid_key_without_running_ssh(self) -> None:
        with patch("fck_rkn.agents.ru_server.subprocess.run") as run:
            self.assertEqual(check_outline_client(self.settings, "bad"), 1)
        run.assert_not_called()


class RuServerForwardCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )

    def test_runs_http_check_on_ru_server_without_secrets(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = check_squid_forward(self.settings)

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertIn("--tunnel-through-iap", command)
        self.assertEqual(run.call_args.kwargs["input"], "")

    def test_script_sends_http_through_forward_and_requires_squid(self) -> None:
        self.assertIn("fck-rkn-squid-forward", FORWARD_CHECK_COMMAND)
        self.assertIn("--proxy http://127.0.0.1:3128", FORWARD_CHECK_COMMAND)
        self.assertIn("squid", FORWARD_CHECK_COMMAND)
        self.assertIn("did not reach EU Squid", FORWARD_CHECK_COMMAND)

    def test_fails_when_remote_check_fails(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR"),
        ):
            self.assertEqual(check_squid_forward(self.settings), 1)
