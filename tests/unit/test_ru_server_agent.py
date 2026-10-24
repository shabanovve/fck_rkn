from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fake_runner import FakeRunner

from fck_rkn.agents.ru_firewall import build_ru_firewall_rules
from fck_rkn.agents.ru_server import (
    apply_ru_firewall,
    build_firewall_apply_command,
    build_client_deploy_command,
    build_forward_deploy_command,
    build_mtproxy_forward_deploy_command,
    build_recovery_command,
    deploy_mtproxy_forward,
    deploy_outline_client,
    deploy_outline_server,
    deploy_squid_forward,
    main,
    parse_access_url,
    recover_tunnel,
    run_outline_squid_probe,
    stop_tunnel,
)
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteTimeoutError


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

    def test_probe_passes_key_via_stdin(self) -> None:
        runner = FakeRunner(stdout="HTTP 403\n")
        result = run_outline_squid_probe(runner, make_access_url())

        self.assertEqual(result, 0)
        self.assertNotIn("test-secret", runner.command)
        self.assertEqual(runner.stdin, "test-method test-secret configured-host 10001\n")
        self.assertIn("ss-tunnel", runner.command)
        self.assertIn(":3128", runner.command)

    def test_probe_fails_when_squid_is_unreachable(self) -> None:
        runner = FakeRunner(exit_code=1)
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            result = run_outline_squid_probe(runner, make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_probe_fails_on_timeout_and_invalid_key(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(
                run_outline_squid_probe(
                    FakeRunner(error=RemoteTimeoutError()), make_access_url()
                ),
                1,
            )
        runner = FakeRunner()
        self.assertEqual(run_outline_squid_probe(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])


class RuServerClientDeployTests(unittest.TestCase):
    def test_deploys_persistent_client_with_config_via_stdin(self) -> None:
        runner = FakeRunner(stdout="started\n")
        result = deploy_outline_client(runner, make_access_url())

        self.assertEqual(result, 0)
        self.assertNotIn("test-secret", runner.command)
        config = json.loads(runner.stdin)
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
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            result = deploy_outline_client(runner, make_access_url())

        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_deploy_rejects_invalid_key_without_running_ssh(self) -> None:
        runner = FakeRunner()
        self.assertEqual(deploy_outline_client(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])


class RuServerSquidForwardTests(unittest.TestCase):
    def test_forwards_localhost_3128_to_eu_squid_with_config_via_stdin(self) -> None:
        runner = FakeRunner(stdout="started\n")
        result = deploy_squid_forward(runner, make_access_url())

        self.assertEqual(result, 0)
        self.assertNotIn("test-secret", runner.command)
        config = json.loads(runner.stdin)
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
        runner = FakeRunner()
        self.assertEqual(recover_tunnel(runner), 0)
        self.assertEqual(runner.stdin, "")
        self.assertEqual(runner.command, build_recovery_command())

    def test_recover_tunnel_fails_when_tunnel_does_not_return(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(recover_tunnel(FakeRunner(exit_code=1)), 1)

    def test_deploys_outline_server_through_the_runner(self) -> None:
        runner = FakeRunner()
        result = deploy_outline_server(runner, "ru.internal")

        self.assertEqual(result, 0)
        self.assertIn("expected_image=fck-rkn-shadowbox:no-public-ip-check", runner.command)
        self.assertIn("--api-port 10000", runner.command)
        self.assertIn("--keys-port 10001", runner.command)
        self.assertIn("leaving it unchanged", runner.command)
        self.assertIn("private_address=ru.internal", runner.command)

    def test_outline_deploy_rejects_blank_private_address(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.outline", level="ERROR"):
            self.assertEqual(deploy_outline_server(runner, " "), 1)
        self.assertEqual(runner.calls, [])

    def test_ru_outline_deploy_reports_failure_and_timeout(self) -> None:
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(deploy_outline_server(FakeRunner(exit_code=7), "ru.internal"), 7)
            self.assertEqual(
                deploy_outline_server(FakeRunner(error=RemoteTimeoutError()), "ru.internal"), 1
            )

    def test_cli_dispatches_ru_deploy_outline(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server.load_agent_settings"),
            patch("fck_rkn.agents.ru_server.ru_server_runner"),
            patch("fck_rkn.agents.ru_server.deploy_outline_server", return_value=0) as deploy,
        ):
            self.assertEqual(main(["deploy-outline"]), 0)
        deploy.assert_called_once()

    def test_stop_tunnel_stops_only_the_forward_container(self) -> None:
        runner = FakeRunner()
        self.assertEqual(stop_tunnel(runner), 0)
        self.assertIn("docker stop", runner.command)
        self.assertIn("fck-rkn-squid-forward", runner.command)
        self.assertNotIn("fck-rkn-outline-client", runner.command)

    def test_cli_dispatches_recover_tunnel(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server.load_agent_settings"),
            patch("fck_rkn.agents.ru_server.ru_server_runner"),
            patch("fck_rkn.agents.ru_server.recover_tunnel", return_value=0) as recover,
        ):
            self.assertEqual(main(["recover-tunnel"]), 0)
        recover.assert_called_once()

    def test_cli_fails_with_exit_2_on_invalid_agent_configuration(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server.load_agent_settings", side_effect=ValueError),
            self.assertLogs("fck_rkn.agents.ru_server", level="ERROR"),
        ):
            self.assertEqual(main(["recover-tunnel"]), 2)

    def test_forward_and_client_use_separate_containers(self) -> None:
        self.assertNotIn("fck-rkn-outline-client", build_forward_deploy_command())
        self.assertNotIn("fck-rkn-squid-forward", build_client_deploy_command())

    def test_forward_fails_without_leaking_output_and_rejects_bad_key(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            result = deploy_squid_forward(runner, make_access_url())
        self.assertEqual(result, 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

        runner = FakeRunner()
        self.assertEqual(deploy_squid_forward(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])


class RuServerMtproxyForwardTests(unittest.TestCase):
    def test_forwards_localhost_8443_to_eu_mtproxy_with_config_via_stdin(self) -> None:
        runner = FakeRunner(stdout="started\n")
        result = deploy_mtproxy_forward(runner, make_access_url())

        self.assertEqual(result, 0)
        self.assertNotIn("test-secret", runner.command)
        config = json.loads(runner.stdin)
        self.assertEqual(config["server"], "configured-host")
        self.assertEqual(config["password"], "test-secret")
        self.assertEqual(config["local_address"], "127.0.0.1")
        self.assertEqual(config["local_port"], 8443)
        self.assertEqual(config["tunnel_address"], "configured-host:8443")

    def test_forward_uses_its_own_container_and_state(self) -> None:
        script = build_mtproxy_forward_deploy_command()
        self.assertIn("ss-tunnel", script)
        self.assertIn("fck-rkn-mtproxy-forward", script)
        self.assertIn("/opt/fck-rkn-mtproxy-forward", script)
        self.assertNotIn("fck-rkn-squid-forward", script)
        self.assertNotIn("fck-rkn-mtproxy-forward", build_forward_deploy_command())
        self.assertNotIn("fck-rkn-mtproxy-forward", build_client_deploy_command())

    def test_forward_fails_without_leaking_output_and_rejects_bad_key(self) -> None:
        runner = FakeRunner(exit_code=1, stdout="test-secret")
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            self.assertEqual(deploy_mtproxy_forward(runner, make_access_url()), 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

        runner = FakeRunner()
        self.assertEqual(deploy_mtproxy_forward(runner, "bad"), 1)
        self.assertEqual(runner.calls, [])

    def test_cli_reads_the_key_from_stdin(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server.load_agent_settings"),
            patch("fck_rkn.agents.ru_server.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.ru_server.deploy_mtproxy_forward", return_value=0
            ) as deploy,
        ):
            stdin.readline.return_value = "ss://key\n"
            self.assertEqual(main(["deploy-mtproxy-forward"]), 0)
        deploy.assert_called_once_with(make_runner.return_value, "ss://key\n")


class RuServerFirewallTests(unittest.TestCase):
    def config(self) -> str:
        return json.dumps(
            {
                "test_client_address": "10.0.0.3",
                "eu_server_address": "10.0.0.2",
                "eu_outline_address": "10.0.0.2",
                "eu_outline_port": 10001,
                "rkn_ipv4": ["192.0.2.0/24"],
                "rkn_ipv6": ["2001:db8::/32"],
                "google_networks": ["198.51.100.0/24"],
                "snapd_networks": ["203.0.113.0/24"],
                "vm_services": {
                    "metadata_address": "169.254.169.254",
                    "dns_addresses": ["169.254.169.254"],
                    "dhcp_server_address": "169.254.169.254",
                    "ntp_addresses": ["169.254.169.254"],
                    "local_networks": ["10.0.0.0/24"],
                },
            }
        )

    def test_passes_generated_rules_to_transactional_remote_installer(self) -> None:
        runner = FakeRunner(stdout="RU firewall rules installed\n")

        self.assertEqual(apply_ru_firewall(runner, self.config()), 0)

        self.assertIn("nft --check --file", runner.command)
        self.assertIn("systemctl reload-or-restart", runner.command)
        self.assertIn("fck-rkn-ru-firewall.service", runner.command)
        self.assertIn("destroy table inet fck_rkn_ru", runner.stdin)

    def test_rejects_invalid_configuration_without_running_remote_commands(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.ru_server", level="ERROR"):
            self.assertEqual(apply_ru_firewall(runner, '{"rkn_ipv4": []}'), 1)
        self.assertEqual(runner.calls, [])

    def test_firewall_command_preserves_a_previous_configuration_on_failure(self) -> None:
        script = build_firewall_apply_command()

        self.assertIn("nft --check --file", script)
        self.assertIn("restore_previous", script)
        self.assertIn("previous configuration", script)
        self.assertIn("reload-or-restart", script)
        self.assertIn("/etc/fck-rkn/ru-firewall.nft", script)

    def test_cli_reads_full_firewall_configuration_from_stdin(self) -> None:
        config = self.config()
        with (
            patch("fck_rkn.agents.ru_server.load_agent_settings"),
            patch("fck_rkn.agents.ru_server.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.ru_server.apply_ru_firewall", return_value=0
            ) as apply,
        ):
            stdin.read.return_value = config
            self.assertEqual(main(["apply-firewall"]), 0)
        apply.assert_called_once_with(make_runner.return_value, config)

    def test_failed_update_restores_saved_rules_and_reapplies_previous_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary_directory = root / "bin"
            binary_directory.mkdir()
            rules_file = root / "etc" / "fck-rkn" / "ru-firewall.nft"
            unit_file = root / "systemd" / "fck-rkn-ru-firewall.service"
            active_file = root / "active-rules.nft"
            lock_file = root / "run" / "ru-firewall.lock"
            lock_file.parent.mkdir()
            script = render(
                "apply_firewall.sh",
                host="ru",
                rules_file=str(rules_file),
                unit_file=str(unit_file),
                lock_file=str(lock_file),
            )
            for name, body in (
                ("sudo", 'exec "$@"\n'),
                (
                    "nft",
                    'if [ "$1" = "--check" ]; then exit 0; fi\n'
                    'if [ "$1" = "-f" ]; then cp "$2" "$ACTIVE_FILE"; exit 0; fi\n'
                    'exit 2\n',
                ),
                (
                    "systemctl",
                    'case "$1" in\n'
                    '  daemon-reload|enable) exit 0 ;;\n'
                    '  reload-or-restart)\n'
                    '    [ "$FAIL_ACTIVATION" != 1 ] || exit 1\n'
                    '    nft -f "$RULES_FILE" ;;\n'
                    '  disable) exit 0 ;;\n'
                    '  *) exit 2 ;;\n'
                    'esac\n',
                ),
            ):
                executable = binary_directory / name
                executable.write_text("#!/bin/sh\n" + body)
                executable.chmod(0o755)

            environment = {
                **os.environ,
                "PATH": f"{binary_directory}:{os.environ['PATH']}",
                "ACTIVE_FILE": str(active_file),
                "RULES_FILE": str(rules_file),
                "FAIL_ACTIVATION": "0",
            }
            previous_rules = build_ru_firewall_rules(self.config())
            first = subprocess.run(
                ["bash", "-c", script],
                input=previous_rules,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(rules_file.read_text(), previous_rules)
            self.assertEqual(active_file.read_text(), previous_rules)

            replacement_config = json.loads(self.config())
            replacement_config["test_client_address"] = "10.0.0.4"
            replacement_rules = build_ru_firewall_rules(
                json.dumps(replacement_config)
            )
            failed = subprocess.run(
                ["bash", "-c", script],
                input=replacement_rules,
                env={**environment, "FAIL_ACTIVATION": "1"},
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("restoring the previous configuration", failed.stderr)
            self.assertEqual(rules_file.read_text(), previous_rules)
            self.assertEqual(active_file.read_text(), previous_rules)
            self.assertEqual(list(rules_file.parent.glob(".ru-firewall-backup.*")), [])
