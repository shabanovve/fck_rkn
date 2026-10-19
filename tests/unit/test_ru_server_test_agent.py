from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

from fck_rkn.agents.ru_server_test import check_outline_squid_route
from fck_rkn.config import TestEnvironmentSettings


def make_access_url() -> str:
    userinfo = base64.urlsafe_b64encode(b"test-method:test-secret").decode()
    return f"ss://{userinfo}@configured-host:10001/?outline=1"


class RuServerTestAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = TestEnvironmentSettings.model_construct(
            GCP_PROJECT_ID="fck-rkn-test",
            GCP_ZONE="europe-north1-a",
        )

    def test_route_check_uses_real_outline_tunnel_to_squid_on_ru_server(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="route ok\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = check_outline_squid_route(self.settings, make_access_url())

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        remote = next(c for c in command if c.startswith("--command="))
        self.assertIn("ss-tunnel", remote)
        self.assertIn(":3128", remote)
        self.assertIn("squid", remote)
        self.assertNotIn("test-secret", " ".join(command))
        self.assertEqual(
            run.call_args.kwargs["input"],
            "test-method test-secret configured-host 10001\n",
        )

    def test_route_check_fails_when_response_is_not_from_squid(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="secret details"
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR") as logs,
        ):
            result = check_outline_squid_route(self.settings, make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("secret details", "\n".join(logs.output))

    def test_route_check_rejects_invalid_key_without_running_ssh(self) -> None:
        with patch("fck_rkn.agents.ru_server.subprocess.run") as run:
            self.assertEqual(check_outline_squid_route(self.settings, "bad"), 1)
        run.assert_not_called()
