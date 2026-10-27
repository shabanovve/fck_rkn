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
    build_crawler_source_check_command,
    build_firewall_check_command,
    build_firewall_check_input,
    build_vpn_client_check_command,
    build_firewall_rollback_check_command,
    OUTLINE_HEALTH_CHECK_COMMAND,
    FORWARD_CHECK_COMMAND,
    MTPROXY_FORWARD_CHECK_COMMAND,
    TUNNEL_BLOCKED_CHECK_COMMAND,
    check_firewall_crawler_source,
    check_firewall_policy_rules,
    check_firewall_vpn_client,
    check_firewall_policy,
    check_firewall_rollback,
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


class RuFirewallCheckTests(unittest.TestCase):
    def config(self) -> str:
        return json.dumps(
            {
                "eu_server_address": "10.0.0.2",
                "eu_outline_address": "10.0.0.2",
                "eu_outline_port": 10001,
                "other_host_address": "10.0.0.4",
                "server_to_github_addresses": ["198.51.100.0/24", "2001:db9:1::/48"],
                "server_to_github_check_hosts": ["github-api.example"],
                "snapd_networks": ["203.0.113.0/24"],
                "vm_services": {
                    "metadata_address": "10.0.0.250",
                    "dns_addresses": ["10.0.0.251"],
                    "dhcp_server_address": "10.0.0.252",
                    "ntp_addresses": ["2001:db8::1", "10.0.0.253"],
                    "local_networks": ["10.0.0.0/24"],
                },
            }
        )

    def test_policy_script_checks_service_access_and_absence_of_denials(
        self,
    ) -> None:
        script = build_firewall_check_command()

        self.assertIn('for port in 80 443; do', script)
        self.assertIn('daddr @$set_name tcp dport', script)
        self.assertIn('DST=$service_ip ', script)
        self.assertIn("No denial is logged for the allowed service addresses", script)

    def test_policy_input_requires_server_to_github_check_hosts(self) -> None:
        for value in (None, [], [""], "host", [5]):
            with self.subTest(value=value):
                config = json.loads(self.config())
                if value is None:
                    del config["server_to_github_check_hosts"]
                else:
                    config["server_to_github_check_hosts"] = value
                with self.assertRaises(ValueError):
                    build_firewall_check_input(json.dumps(config))

    def test_policy_script_requires_default_deny_and_logged_denials(self) -> None:
        script = build_firewall_check_command()

        self.assertIn("policy drop", script)
        self.assertIn("test-client", script)
        self.assertIn("rkn_ipv4", script)
        for prefix in (
            "fck-rkn ru input crawler-v4:",
            "fck-rkn ru input denied:",
            "fck-rkn ru output direct-eu-service:",
            "fck-rkn ru output denied:",
            "fck-rkn ru forward denied:",
        ):
            self.assertIn(prefix, script)
        self.assertIn("vpn_port=10001", script)
        self.assertIn("proxy_ports=(3128 8443)", script)
        self.assertIn("udp sport 67 udp dport 68", script)
        self.assertIn("journalctl -k", script)
        self.assertIn("getent hosts", script)
        self.assertNotIn("expect_reachable", script)

    def test_scripts_have_valid_syntax(self) -> None:
        for script in (
            build_firewall_check_command(),
            build_vpn_client_check_command(),
            build_crawler_source_check_command(),
        ):
            result = subprocess.run(
                ["bash", "-n"], input=script, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_input_contains_only_derived_non_secret_values(self) -> None:
        data = json.loads(build_firewall_check_input(self.config()))

        self.assertEqual(data["dhcp_address"], "10.0.0.252")
        self.assertEqual(data["vm_service_address"], "10.0.0.250")
        self.assertEqual(data["ntp_address"], "10.0.0.253")
        self.assertEqual(data["ssh_port"], 22)
        self.assertEqual(
            data["service_sets"], ["server_to_github_ipv4", "server_to_github_ipv6", "snapd_ipv4"]
        )
        self.assertEqual(
            data["service_hosts"], ["github-api.example", "api.snapcraft.io"]
        )
        self.assertEqual(data["eu_outline_port"], 10001)
        self.assertNotIn("test_client_address", data)

    def test_policy_rules_check_passes_runtime_addresses_only_over_stdin(self) -> None:
        runner = FakeRunner(stdout="blocked\n")

        self.assertEqual(check_firewall_policy_rules(runner, self.config()), 0)

        self.assertEqual(runner.command, build_firewall_check_command())
        self.assertEqual(json.loads(runner.stdin)["other_host_address"], "10.0.0.4")
        self.assertNotIn("10.0.0.4", runner.command)

    def test_policy_rules_check_rejects_invalid_or_ambiguous_hosts(self) -> None:
        runner = FakeRunner()
        ambiguous = json.loads(self.config())
        ambiguous["other_host_address"] = "10.0.0.2"
        for config in ('{"other_host_address":"host"}', json.dumps(ambiguous)):
            with self.subTest(config=config), self.assertLogs(
                "fck_rkn.agents.ru_server_test", level="ERROR"
            ):
                self.assertEqual(check_firewall_policy_rules(runner, config), 1)
        self.assertEqual(runner.calls, [])

    def test_crawler_source_script_requires_denial_and_log(self) -> None:
        script = build_crawler_source_check_command()

        self.assertIn("reached the RU test listener", script)
        self.assertIn("did not count the crawler-range request", script)
        self.assertIn("denied and logged", script)
        self.assertNotIn("logged and allowed", script)

        runner = FakeRunner()
        self.assertEqual(
            check_firewall_crawler_source(runner, "10.0.0.1", "192.0.2.0"), 0
        )
        self.assertEqual(runner.command, script)

    def test_vpn_client_check_passes_the_key_only_over_stdin(self) -> None:
        runner = FakeRunner(stdout="ok\n")

        self.assertEqual(
            check_firewall_vpn_client(runner, make_access_url(), self.config()), 0
        )

        script = build_vpn_client_check_command()
        self.assertEqual(runner.command, script)
        self.assertNotIn("test-secret", script)
        data = json.loads(runner.stdin)
        self.assertEqual(data["client"]["password"], "test-secret")
        self.assertEqual(data["other_host_address"], "10.0.0.4")
        self.assertEqual(data["eu_server_address"], "10.0.0.2")
        self.assertEqual(data["ssh_port"], 22)

    def test_vpn_client_script_checks_reachable_and_denied_targets(self) -> None:
        script = build_vpn_client_check_command()

        self.assertIn("docker image inspect", script)
        self.assertNotIn("docker pull", script)
        self.assertIn("--network host", script)
        self.assertIn("ru-server itself", script)
        self.assertIn("web proxy", script)
        self.assertIn("MTProto proxy", script)
        self.assertIn("reached an external host", script)
        self.assertIn("fck-rkn ru output direct-eu-service:", script)
        self.assertIn("fck-rkn ru output denied:", script)
        self.assertIn("docker rm -f", script)

    def test_vpn_client_check_rejects_an_invalid_key(self) -> None:
        runner = FakeRunner()
        with self.assertLogs("fck_rkn.agents.ru_server_test", level="ERROR"):
            self.assertEqual(
                check_firewall_vpn_client(runner, "not-a-key", self.config()), 1
            )
        self.assertEqual(runner.calls, [])

    def test_cli_dispatches_vpn_client_check(self) -> None:
        config = self.config()
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server_test.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.ru_server_test.check_firewall_vpn_client",
                return_value=0,
            ) as check,
        ):
            stdin.readline.return_value = "ss://key\n"
            stdin.read.return_value = config
            self.assertEqual(main(["firewall-vpn-client"]), 0)
        check.assert_called_once_with(make_runner.return_value, "ss://key\n", config)

    def test_policy_check_requires_squid_and_mtproxy_forward_checks(self) -> None:
        runner = FakeRunner()
        with (
            patch(
                "fck_rkn.agents.ru_server_test.check_squid_forward", return_value=0
            ) as check_squid,
            patch(
                "fck_rkn.agents.ru_server_test.check_mtproxy_forward",
                return_value=0,
            ) as check_mtproxy,
            patch(
                "fck_rkn.agents.ru_server_test.check_firewall_policy_rules",
                return_value=0,
            ) as check_egress,
        ):
            self.assertEqual(check_firewall_policy(runner, self.config()), 0)
        check_squid.assert_called_once_with(runner)
        check_mtproxy.assert_called_once_with(runner)
        check_egress.assert_called_once_with(runner, self.config())

    def test_failed_allowed_path_fails_policy_check(self) -> None:
        runner = FakeRunner()
        with (
            patch(
                "fck_rkn.agents.ru_server_test.check_squid_forward", return_value=1
            ),
            patch(
                "fck_rkn.agents.ru_server_test.check_mtproxy_forward"
            ) as check_mtproxy,
            self.assertLogs("fck_rkn.agents.ru_server_test", level="ERROR"),
        ):
            self.assertEqual(check_firewall_policy(runner, self.config()), 1)
        check_mtproxy.assert_not_called()

    def test_rollback_check_invokes_transactional_installer(self) -> None:
        script = build_firewall_rollback_check_command()
        encoded = script.split("printf '%s' '", 1)[1].split("'", 1)[0]
        embedded_installer = base64.b64decode(encoded).decode()

        self.assertIn("invalid.nft", script)
        self.assertIn("active-before", script)
        self.assertIn("saved-before", script)
        self.assertIn("initial-apply-firewall.sh", script)
        self.assertIn("Invalid initial firewall configuration", script)
        self.assertIn("nftables rejected the firewall candidate", script)
        self.assertIn("restore_previous", embedded_installer)

        runner = FakeRunner(stdout="preserved\n")
        self.assertEqual(check_firewall_rollback(runner), 0)
        self.assertEqual(runner.command, script)
        self.assertIsNone(runner.stdin)

    def test_cli_dispatches_policy_check_with_configuration_on_stdin(self) -> None:
        config = self.config()
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch("fck_rkn.agents.ru_server_test.sys.stdin") as stdin,
            patch(
                "fck_rkn.agents.ru_server_test.check_firewall_policy",
                return_value=0,
            ) as check,
        ):
            stdin.read.return_value = config
            self.assertEqual(main(["firewall-policy"]), 0)
        check.assert_called_once_with(make_runner.return_value, config)

    def test_cli_dispatches_rollback_check_without_configuration(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.ru_server_test.check_firewall_rollback",
                return_value=0,
            ) as check,
        ):
            self.assertEqual(main(["firewall-rollback"]), 0)
        check.assert_called_once_with(make_runner.return_value)

    def test_cli_dispatches_blacklist_updater_check(self) -> None:
        with (
            patch("fck_rkn.agents.ru_server_test.load_agent_settings"),
            patch("fck_rkn.agents.ru_server_test.ru_server_runner") as make_runner,
            patch(
                "fck_rkn.agents.ru_server_test.check_rkn_blacklist_updater",
                return_value=0,
            ) as check,
        ):
            self.assertEqual(main(["firewall-blacklist"]), 0)
        check.assert_called_once_with(make_runner.return_value, "ru")


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
    check_firewall_crawler_source,
    check_firewall_policy_rules,
    check_firewall_vpn_client,
    check_firewall_policy,
    check_firewall_rollback,
