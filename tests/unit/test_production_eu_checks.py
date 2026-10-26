"""Production EU stage: ordering and safety of each check (tasks 214 onward)."""

from __future__ import annotations

import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from fck_rkn.agents.eu_server_test import (
    SQUID_BLOCK_CHECK_COMMAND,
    SQUID_HTTP_CHECK_COMMAND,
    SQUID_STARTUP_CHECK_COMMAND,
)
from fck_rkn.orchestration import production_environment
from fck_rkn.remote.runner import RemoteResult, RemoteTimeoutError

MUTATING_COMMANDS = re.compile(
    r"docker\s+(run|rm|stop|restart|kill|start|pull|load|exec)\b"
    r"|\b(systemctl|iptables|ip6tables|nft|ipset|apt-get|apt|rm|mv|tee)\b"
    r"|>\s*/(etc|opt|var|usr)"
)


class ScriptedRunner:
    """Returns a scripted result per call and records every command."""

    def __init__(self, results: list[RemoteResult | Exception] | None = None) -> None:
        self.results = list(results or [])
        self.commands: list[str] = []

    def run(self, command, stdin, timeout, stream_output=False):
        self.commands.append(command)
        outcome = self.results.pop(0) if self.results else RemoteResult(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run_deployment(runner: ScriptedRunner) -> int:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        return production_environment.deploy_eu_squid(runner)


class SquidStartupCheckTests(unittest.TestCase):
    """Task 214."""

    def test_startup_check_runs_right_after_the_deployment(self) -> None:
        runner = ScriptedRunner()

        self.assertEqual(run_deployment(runner), 0)

        self.assertEqual(runner.commands[1], SQUID_STARTUP_CHECK_COMMAND)
        self.assertNotEqual(runner.commands[0], SQUID_STARTUP_CHECK_COMMAND)

    def test_failed_startup_check_stops_all_later_eu_actions(self) -> None:
        for label, outcome in (
            ("check failure", RemoteResult(1, stderr="not running")),
            ("timeout", RemoteTimeoutError()),
        ):
            with self.subTest(case=label):
                runner = ScriptedRunner([RemoteResult(0), outcome])

                self.assertNotEqual(run_deployment(runner), 0)

                self.assertEqual(len(runner.commands), 2)

    def test_failed_deployment_never_reaches_the_startup_check(self) -> None:
        runner = ScriptedRunner([RemoteResult(5)])

        self.assertEqual(run_deployment(runner), 5)

        self.assertNotIn(SQUID_STARTUP_CHECK_COMMAND, runner.commands)

    def test_startup_check_does_not_change_service_or_firewall_state(self) -> None:
        self.assertIsNone(MUTATING_COMMANDS.search(SQUID_STARTUP_CHECK_COMMAND))
        self.assertIn("docker inspect", SQUID_STARTUP_CHECK_COMMAND)


if __name__ == "__main__":
    unittest.main()
