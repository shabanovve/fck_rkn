from __future__ import annotations

import base64
import subprocess
import unittest
from unittest.mock import patch

import json

from fck_rkn.agents.ru_server import (
    build_client_deploy_command,
    build_forward_deploy_command,
    build_recovery_command,
    deploy_outline_client,
    deploy_squid_forward,
    main,
    parse_access_url,
    recover_tunnel,
    run_outline_squid_probe,
)
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


class RuServerClientDeployTests(unittest.TestCase):
    def test_deploys_persistent_client_with_config_via_stdin(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="started\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = deploy_outline_client(make_settings(), make_access_url())

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertNotIn("test-secret", " ".join(command))
        config = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(config["server"], "configured-host")
        self.assertEqual(config["server_port"], 10001)
        self.assertEqual(config["method"], "test-method")
        self.assertEqual(config["password"], "test-secret")
        self.assertEqual(config["local_address"], "127.0.0.1")

    def test_client_script_is_private_idempotent_and_restarts(self) -> None:
        script = build_client_deploy_command()
        self.assertIn("ss-local", script)
        self.assertIn("--restart unless-stopped", script)
        self.assertIn("install -d -m 700", script)
        self.assertIn("umask 077", script)
        self.assertIn("leaving it unchanged", script)
        self.assertIn("refusing to replace it", script)
        self.assertIn("--user root", script)
        self.assertIn("is not running; recreating it", script)
        self.assertNotIn("test-secret", script)

    def test_deploy_fails_on_remote_error_without_leaking_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="test-secret"
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR") as logs,
        ):
            result = deploy_outline_client(make_settings(), make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_deploy_rejects_invalid_key_without_running_ssh(self) -> None:
        with patch("fck_rkn.agents.ru_server.subprocess.run") as run:
            self.assertEqual(deploy_outline_client(make_settings(), "bad"), 1)
        run.assert_not_called()


class RuServerSquidForwardTests(unittest.TestCase):
    def test_forwards_localhost_3128_to_eu_squid_with_config_via_stdin(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="started\n", stderr=""
        )
        with patch(
            "fck_rkn.agents.ru_server.subprocess.run", return_value=completed
        ) as run:
            result = deploy_squid_forward(make_settings(), make_access_url())

        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertNotIn("test-secret", " ".join(command))
        config = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(config["server"], "configured-host")
        self.assertEqual(config["password"], "test-secret")
        self.assertEqual(config["local_address"], "127.0.0.1")
        self.assertEqual(config["local_port"], 3128)
        self.assertEqual(config["tunnel_address"], "configured-host:3128")

    def test_forward_script_is_private_idempotent_and_restarts(self) -> None:
        script = build_forward_deploy_command()
        self.assertIn("ss-tunnel", script)
        self.assertIn("fck-rkn-squid-forward", script)
        self.assertIn("--restart unless-stopped", script)
        self.assertIn("install -d -m 700", script)
        self.assertIn("umask 077", script)
        self.assertIn("leaving it unchanged", script)
        self.assertIn("--user root", script)
        self.assertIn("is not running; recreating it", script)
        self.assertNotIn("test-secret", script)

    def test_recovery_script_restarts_tunnel_with_bounded_retries(self) -> None:
        script = build_recovery_command()
        self.assertIn("attempts=5", script)
        self.assertIn("docker restart", script)
        self.assertIn("fck-rkn-outline-client", script)
        self.assertIn("fck-rkn-squid-forward", script)
        self.assertIn("--proxy http://127.0.0.1:3128", script)
        self.assertIn("client traffic stays blocked", script)
        for forbidden in ("iptables", "ip route", "nft", "--noproxy"):
            self.assertNotIn(forbidden, script)

    def test_recover_tunnel_runs_script_without_stdin_secrets(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed) as run:
            result = recover_tunnel(make_settings())

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.kwargs["input"], "")
        self.assertIn(f"--command={build_recovery_command()}", run.call_args.args[0])

    def test_recover_tunnel_fails_when_tunnel_does_not_return(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="x")
        with patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed):
            self.assertEqual(recover_tunnel(make_settings()), 1)

    def test_cli_dispatches_recover_tunnel(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server.load_config", return_value=make_settings()),
            patch("fck_rkn.agents.ru_server.recover_tunnel", return_value=0) as recover,
        ):
            self.assertEqual(main(["recover-tunnel"]), 0)
        recover.assert_called_once()

    def test_forward_and_client_use_separate_containers(self) -> None:
        self.assertNotIn("fck-rkn-outline-client", build_forward_deploy_command())
        self.assertNotIn("fck-rkn-squid-forward", build_client_deploy_command())

    def test_forward_fails_without_leaking_output_and_rejects_bad_key(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="test-secret"
        )
        with (
            patch("fck_rkn.agents.ru_server.subprocess.run", return_value=completed),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR") as logs,
        ):
            result = deploy_squid_forward(make_settings(), make_access_url())
        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

        with patch("fck_rkn.agents.ru_server.subprocess.run") as run:
            self.assertEqual(deploy_squid_forward(make_settings(), "bad"), 1)
        run.assert_not_called()
