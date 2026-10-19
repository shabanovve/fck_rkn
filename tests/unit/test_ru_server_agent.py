from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

from fck_rkn.agents.ru_server import parse_access_url, run_outline_squid_probe
from fck_rkn.config import TestEnvironmentSettings


def make_settings() -> TestEnvironmentSettings:
    return TestEnvironmentSettings.model_construct(
        GCP_PROJECT_ID="fck-rkn-test",
        GCP_ZONE="europe-north1-a",
    )


def make_access_url() -> str:
    userinfo = base64.urlsafe_b64encode(b"test-method:test-secret").decode()
    return f"ss://{userinfo}@configured-host:10001/?outline=1#name"


class RuServerAgentTests(unittest.TestCase):
    def test_parses_outline_access_url(self) -> None:
        self.assertEqual(
            parse_access_url(make_access_url()),
            ("test-method", "test-secret", "configured-host", 10001),
        )

    def test_rejects_malformed_access_urls(self) -> None:
        for url in ("http://host:1", "ss://host:1", "ss://!!!@host:1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_access_url(url)

    def test_probe_passes_key_via_stdin_and_runs_on_ru_server(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="HTTP 403\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = run_outline_squid_probe(make_settings(), make_access_url())

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertIn("--tunnel-through-iap", command)
        self.assertNotIn("test-secret", " ".join(command))
        self.assertEqual(
            run.call_args.kwargs["input"],
            "test-method test-secret configured-host 10001\n",
        )
        remote = next(c for c in command if c.startswith("--command="))
        self.assertIn("ss-tunnel", remote)
        self.assertIn(":3128", remote)

    def test_probe_fails_when_squid_is_unreachable(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="secret details"
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR") as logs,
        ):
            result = run_outline_squid_probe(make_settings(), make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("secret details", "\n".join(logs.output))

    def test_probe_fails_on_timeout_and_invalid_key(self) -> None:
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gcloud", 1),
        ):
            self.assertEqual(
                run_outline_squid_probe(make_settings(), make_access_url()), 1
            )
        with patch("fck_rkn.agents.ru_server.subprocess.run") as run:
            self.assertEqual(run_outline_squid_probe(make_settings(), "bad"), 1)
        run.assert_not_called()
