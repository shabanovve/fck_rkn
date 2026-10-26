from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from fck_rkn import production_setup as setup
from fck_rkn.production_config import PROD_TEMPLATE_FILE, load_eu_production_settings
from fck_rkn.remote.runner import RemoteResult, RemoteStartError


class ScriptedRunner:
    def __init__(self, outputs: dict[str, tuple[int, str]], connect: int = 0) -> None:
        self.outputs = outputs
        self.connect = connect
        self.commands: list[str] = []

    def run(self, command, stdin, timeout, stream_output=False):
        self.commands.append(command)
        if command == "true":
            return RemoteResult(self.connect)
        code, out = self.outputs.get(command, (1, ""))
        return RemoteResult(code, out)


FULL = {
    setup.LOCAL_NETWORKS_COMMAND: (
        0,
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0\n"
        "3: docker0    inet 172.17.0.1/16 scope global docker0\n",
    ),
    setup.DNS_COMMAND: (
        0,
        "Global: \nLink 2 (eth0): 192.0.2.53 2001:db8::53\nnameserver 127.0.0.53\n",
    ),
    setup.DHCP_COMMAND: (0, "SERVER_ADDRESS=192.0.2.2\n"),
    setup.NTP_COMMAND: (0, "CurrentNTPServer=192.0.2.123\nSystemNTPServers=pool.ntp.org\n"),
    setup.METADATA_COMMAND: (0, ""),
}


class Session:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.lines: list[str] = []

    def prompt(self, text: str) -> str:
        self.prompts.append(text)
        return self.answers.pop(0)

    def output(self, text: str) -> None:
        self.lines.append(text)


ANSWERS = [
    "192.0.2.10",
    "deploy",
    "/keys/id",
    "",
    "192.0.2.20",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "198.51.100.0/24",
    "y",
]


class ProductionSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "prod.env"

    def run_setup(self, answers, runner, factory_log=None):
        session = Session(answers)

        def factory(host, user, key, port):
            if factory_log is not None:
                factory_log.append((host, user, key, port))
            return runner

        setup.configure_interactively(
            factory, session.prompt, session.output, self.path, PROD_TEMPLATE_FILE
        )
        return session

    def test_parsers(self) -> None:
        self.assertEqual(
            setup.parse_local_networks(FULL[setup.LOCAL_NETWORKS_COMMAND][1]),
            ["192.0.2.0/24"],
        )
        self.assertEqual(
            setup.parse_dns_addresses(FULL[setup.DNS_COMMAND][1]),
            ["192.0.2.53", "2001:db8::53"],
        )
        self.assertEqual(
            setup.parse_dhcp_server("DHCP4.OPTION[3]:dhcp_server_identifier = 192.0.2.2"),
            ["192.0.2.2"],
        )
        self.assertEqual(
            setup.parse_dhcp_server("option dhcp-server-identifier 192.0.2.2;"),
            ["192.0.2.2"],
        )
        self.assertEqual(
            setup.parse_dhcp_server(
                "ens4: DHCPv4 address 10.166.0.2/32, gateway 10.166.0.1 "
                "acquired from 169.254.169.254"
            ),
            ["169.254.169.254"],
        )
        self.assertEqual(
            setup.parse_ntp_addresses("^* 192.0.2.123 2 6 377 1 +1us\nMS Name/IP\n"),
            ["192.0.2.123"],
        )

    def test_successful_discovery_writes_valid_private_config(self) -> None:
        log: list = []
        runner = ScriptedRunner(FULL)
        session = self.run_setup(ANSWERS, runner, log)

        self.assertEqual(
            log,
            [
                ("192.0.2.10", "deploy", Path("/keys/id"), 22),
                ("192.0.2.20", "deploy", Path("/keys/id"), 22),
            ],
        )
        settings = load_eu_production_settings(self.path)
        self.assertEqual(settings.RU_SERVER_ADDRESS, "192.0.2.20")
        self.assertEqual(settings.RU_LOCAL_NETWORKS, ["192.0.2.0/24"])
        self.assertEqual(settings.RU_DNS_ADDRESSES, ["192.0.2.53", "2001:db8::53"])
        self.assertEqual(settings.RU_DHCP_SERVER_ADDRESS, "192.0.2.2")
        self.assertEqual(settings.RU_NTP_ADDRESSES, ["192.0.2.123"])
        self.assertEqual(settings.RU_PROVIDER_NETWORKS, ["198.51.100.0/24"])
        self.assertEqual(settings.EU_LOCAL_NETWORKS, ["192.0.2.0/24"])
        self.assertEqual(settings.EU_DNS_ADDRESSES, ["192.0.2.53", "2001:db8::53"])
        self.assertEqual(settings.EU_DHCP_SERVER_ADDRESS, "192.0.2.2")
        self.assertEqual(settings.EU_NTP_ADDRESSES, ["192.0.2.123"])
        self.assertEqual(settings.EU_METADATA_ADDRESS, "169.254.169.254")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertIn("# IPv4 or IPv6 address assigned to eu-server", self.path.read_text())
        self.assertTrue(any("detected" in prompt for prompt in session.prompts))
        self.assertTrue(any("RU_PROVIDER_NETWORKS" in prompt for prompt in session.prompts))

    def test_discovery_only_runs_read_only_queries(self) -> None:
        runner = ScriptedRunner(FULL)
        self.run_setup(ANSWERS, runner)
        for command in runner.commands:
            command = command.replace("sudo -n journalctl -b --no-pager", "")
            for forbidden in ("rm ", "systemctl", "sudo", ">/etc", "apt"):
                self.assertNotIn(forbidden, command)

    def test_undetected_values_are_prompted_and_invalid_input_reprompts(self) -> None:
        runner = ScriptedRunner({})
        answers = [
            "192.0.2.10",
            "deploy",
            "/keys/id",
            "2222",
            "192.0.2.20",
            "bad",
            "192.0.2.1",
            "192.0.2.53, 192.0.2.54",
            "192.0.2.2",
            "192.0.2.123",
            "192.0.2.0/24",
            "192.0.2.1",
            "192.0.2.54, 2001:db8::54",
            "192.0.2.3",
            "192.0.2.124",
            "192.0.3.0/24",
            "198.51.100.0/24, 2001:db8:abcd::/48",
            "y",
        ]
        session = self.run_setup(answers, runner)

        settings = load_eu_production_settings(self.path)
        self.assertEqual(settings.SSH_PORT, 2222)
        self.assertEqual(settings.EU_DNS_ADDRESSES, ["192.0.2.53", "192.0.2.54"])
        self.assertEqual(settings.RU_PROVIDER_NETWORKS, ["198.51.100.0/24", "2001:db8:abcd::/48"])
        self.assertTrue(any("EU_METADATA_ADDRESS" in l for l in session.lines))

    def test_ssh_failure_writes_nothing(self) -> None:
        runner = ScriptedRunner(FULL, connect=255)
        with self.assertRaises(setup.ProductionSetupError):
            self.run_setup(ANSWERS[:4], runner)
        self.assertFalse(self.path.exists())

    def test_ru_ssh_failure_writes_nothing(self) -> None:
        runner = ScriptedRunner(FULL, connect=255)
        # The first (EU) connection succeeds; the RU connection then fails.
        runner.connects = [0, 255]

        original_run = runner.run
        connection_index = 0

        def run(command, stdin, timeout, stream_output=False):
            nonlocal connection_index
            if command == "true":
                result = runner.connects[connection_index]
                connection_index += 1
                return RemoteResult(result)
            return original_run(command, stdin, timeout, stream_output)

        runner.run = run
        with self.assertRaises(setup.ProductionSetupError):
            self.run_setup(ANSWERS, runner)
        self.assertFalse(self.path.exists())

    def test_ru_configuration_failure_does_not_prevent_otherwise_valid_eu_only_run(self) -> None:
        eu_only_content = (
            "EU_SERVER_ADDRESS=192.0.2.10\n"
            "SSH_USER=deploy\n"
            "SSH_KEY_PATH=/keys/id\n"
            "SSH_PORT=22\n"
            "RU_SERVER_ADDRESS=192.0.2.20\n"
            "EU_METADATA_ADDRESS=192.0.2.1\n"
            'EU_DNS_ADDRESSES=["192.0.2.53"]\n'
            "EU_DHCP_SERVER_ADDRESS=192.0.2.2\n"
            'EU_NTP_ADDRESSES=["192.0.2.123"]\n'
            'EU_LOCAL_NETWORKS=["192.0.2.0/24"]\n'
        )
        self.path.write_text(eu_only_content)
        runner = ScriptedRunner(FULL, connect=255)
        runner.connects = [0, 255]
        connection_index = 0

        original_run = runner.run
        def run(command, stdin, timeout, stream_output=False):
            nonlocal connection_index
            if command == "true":
                result = runner.connects[connection_index]
                connection_index += 1
                return RemoteResult(result)
            return original_run(command, stdin, timeout, stream_output)

        runner.run = run
        with self.assertRaises(setup.ProductionSetupError):
            self.run_setup(["y"] + ANSWERS, runner)
        self.assertEqual(self.path.read_text(), eu_only_content)
        loaded = load_eu_production_settings(self.path)
        self.assertEqual(loaded.EU_SERVER_ADDRESS, "192.0.2.10")
        self.assertIsNone(loaded.RU_LOCAL_NETWORKS)

    def test_declined_confirmation_writes_nothing(self) -> None:
        with self.assertRaises(setup.ProductionSetupError):
            self.run_setup(ANSWERS[:-1] + ["n"], ScriptedRunner(FULL))
        self.assertFalse(self.path.exists())

    def test_existing_file_is_kept_unless_confirmed(self) -> None:
        self.path.write_text("KEEP=1\n")
        with self.assertRaises(setup.ProductionSetupError):
            self.run_setup(["n"], ScriptedRunner(FULL))
        self.assertEqual(self.path.read_text(), "KEEP=1\n")

        self.run_setup(["y"] + ANSWERS, ScriptedRunner(FULL))
        load_eu_production_settings(self.path)

    def test_unstartable_discovery_falls_back_to_prompts(self) -> None:
        class Broken(ScriptedRunner):
            def run(self, command, stdin, timeout, stream_output=False):
                if command == "true":
                    return RemoteResult(0)
                raise RemoteStartError("x")

        found = setup.discover_eu_settings(Broken({}))
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
