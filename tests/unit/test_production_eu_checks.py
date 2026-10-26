"""Production EU stage: ordering and safety of each check (tasks 214 onward)."""

from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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
        self.stdins: list[object] = []

    def run(self, command, stdin, timeout, stream_output=False):
        self.commands.append(command)
        self.stdins.append(stdin)
        outcome = self.results.pop(0) if self.results else RemoteResult(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run_stage(runner: ScriptedRunner, archive: Path | None) -> int:
    with (
        patch.object(
            production_environment, "retrieve_outline_artifact", return_value=archive
        ),
        redirect_stdout(StringIO()),
        redirect_stderr(StringIO()),
    ):
        return production_environment.run_eu_stage(runner)


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


class SquidHttpCheckTests(unittest.TestCase):
    """Task 215."""

    def test_http_check_is_a_distinct_step_after_the_startup_check(self) -> None:
        runner = ScriptedRunner()

        self.assertEqual(run_deployment(runner), 0)

        self.assertEqual(
            runner.commands[1:3],
            [SQUID_STARTUP_CHECK_COMMAND, SQUID_HTTP_CHECK_COMMAND],
        )

    def test_failed_http_check_stops_the_stage(self) -> None:
        for label, outcome in (
            ("error status", RemoteResult(1, stdout="503")),
            ("timeout", RemoteTimeoutError()),
        ):
            with self.subTest(case=label):
                runner = ScriptedRunner([RemoteResult(0), RemoteResult(0), outcome])

                self.assertNotEqual(run_deployment(runner), 0)

                self.assertEqual(len(runner.commands), 3)
                self.assertNotIn(SQUID_BLOCK_CHECK_COMMAND, runner.commands)

    def test_http_check_accepts_only_success_and_redirect_statuses(self) -> None:
        self.assertIn("not 200 <= status < 400", SQUID_HTTP_CHECK_COMMAND)

    def test_http_check_prints_only_the_status_and_sends_no_credentials(self) -> None:
        self.assertIn("print(status)", SQUID_HTTP_CHECK_COMMAND)
        self.assertNotIn(".read(", SQUID_HTTP_CHECK_COMMAND)
        self.assertNotIn("getheaders", SQUID_HTTP_CHECK_COMMAND)
        self.assertNotRegex(
            SQUID_HTTP_CHECK_COMMAND, r"(?i)authorization|password|secret|token"
        )
        self.assertIsNone(MUTATING_COMMANDS.search(SQUID_HTTP_CHECK_COMMAND))

    def test_failure_log_does_not_include_remote_output(self) -> None:
        runner = ScriptedRunner(
            [RemoteResult(0), RemoteResult(0), RemoteResult(1, "BODY-LEAK", "ERR-LEAK")]
        )
        with self.assertLogs(level="ERROR") as logs:
            run_deployment(runner)

        self.assertNotIn("BODY-LEAK", "\n".join(logs.output))


class SquidBlockingCheckTests(unittest.TestCase):
    """Task 216."""

    def test_blocking_check_runs_last_after_the_http_check(self) -> None:
        runner = ScriptedRunner()

        self.assertEqual(run_deployment(runner), 0)

        self.assertEqual(runner.commands[3], SQUID_BLOCK_CHECK_COMMAND)
        self.assertEqual(len(runner.commands), 4)

    def test_failed_blocking_check_fails_the_stage(self) -> None:
        for label, outcome in (
            ("destination not denied", RemoteResult(1, stdout="GET example.ru: 200")),
            ("timeout", RemoteTimeoutError()),
        ):
            with self.subTest(case=label):
                runner = ScriptedRunner([RemoteResult(0)] * 3 + [outcome])

                self.assertNotEqual(run_deployment(runner), 0)

    def test_check_covers_ru_domains_and_ip_literals_and_expects_denial(self) -> None:
        for destination in (
            "example.ru",
            "www.example.su",
            "example.xn--p1ai",
            "192.0.2.1",
            "[2001:db8::1]",
            "3221225985",
        ):
            self.assertIn(destination, SQUID_BLOCK_CHECK_COMMAND)
        self.assertIn('status != "403"', SQUID_BLOCK_CHECK_COMMAND)
        self.assertIn("CONNECT", SQUID_BLOCK_CHECK_COMMAND)

    def test_check_is_read_only_and_sends_no_credentials_or_keys(self) -> None:
        self.assertIsNone(MUTATING_COMMANDS.search(SQUID_BLOCK_CHECK_COMMAND))
        self.assertNotRegex(
            SQUID_BLOCK_CHECK_COMMAND,
            r"(?i)authorization|password|secret|token|ss://|accessurl",
        )


class OutlineImageLoadTests(unittest.TestCase):
    """Task 219."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive = Path(directory.name) / "outline-image.tar.gz"
        self.archive.write_bytes(b"verified archive")

    def test_verified_archive_is_streamed_through_stdin_after_the_squid_checks(
        self,
    ) -> None:
        runner = ScriptedRunner()

        self.assertEqual(run_stage(runner, self.archive), 0)

        self.assertEqual(len(runner.commands), 5)
        self.assertEqual(runner.commands[3], SQUID_BLOCK_CHECK_COMMAND)
        self.assertEqual(runner.stdins[4], self.archive)
        self.assertIn(str(self.archive.stat().st_size), runner.commands[4])
        self.assertNotIn(str(self.archive), runner.commands[4])

    def test_load_command_skips_an_image_that_is_already_present(self) -> None:
        runner = ScriptedRunner()
        run_stage(runner, self.archive)

        load_command = runner.commands[4]
        self.assertIn("docker image inspect", load_command)
        self.assertLess(
            load_command.index("already present"), load_command.index("docker load")
        )

    def test_failed_or_timed_out_load_fails_the_stage_and_names_the_step(self) -> None:
        for label, outcome in (
            ("failure", RemoteResult(7)),
            ("timeout", RemoteTimeoutError()),
        ):
            with self.subTest(case=label):
                runner = ScriptedRunner([RemoteResult(0)] * 4 + [outcome])
                with self.assertLogs(production_environment.logger, level="ERROR") as logs:
                    self.assertNotEqual(run_stage(runner, self.archive), 0)

                self.assertIn("Outline image load", logs.output[-1])

    def test_unverified_artifact_never_reaches_the_server(self) -> None:
        runner = ScriptedRunner()
        with self.assertLogs(production_environment.logger, level="ERROR") as logs:
            self.assertEqual(run_stage(runner, None), 1)

        self.assertEqual(len(runner.commands), 4)
        self.assertIn("Outline image load", logs.output[-1])

    def test_squid_failure_prevents_the_image_load(self) -> None:
        runner = ScriptedRunner([RemoteResult(0), RemoteResult(0), RemoteResult(1)])

        self.assertNotEqual(run_stage(runner, self.archive), 0)

        self.assertEqual(len(runner.commands), 3)


if __name__ == "__main__":
    unittest.main()
