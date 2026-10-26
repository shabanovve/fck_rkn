"""Test-only operations that the production EU stage must never run (tasks 228+)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_production_eu_checks import ACCESS_URL, ScriptedRunner, run_stage

from fck_rkn import last_run
from fck_rkn.agents import eu_server_test, test_client
from fck_rkn.gcp import compute
from fck_rkn.orchestration import production_environment, test_environment

PRODUCTION_SOURCES = (
    Path(production_environment.__file__),
    Path(production_environment.__file__).parents[1] / "production_firewall.py",
    Path(production_environment.__file__).parents[1] / "production_secrets.py",
    Path(production_environment.__file__).with_name("outline_artifact.py"),
)


def production_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_SOURCES)


class StageRun:
    """Runs the stage with every named attribute of a module replaced by a trap."""

    def __init__(self, test: unittest.TestCase) -> None:
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.archive = Path(directory.name) / "outline-image.tar.gz"
        self.archive.write_bytes(b"verified archive")
        self.runner = ScriptedRunner()

    def run(self) -> int:
        return run_stage(self.runner, self.archive)


def trap_attributes(test: unittest.TestCase, module, names: tuple[str, ...]) -> list:
    """Replace each named module attribute with a mock that fails when called."""
    traps = []
    for name in names:
        trap = patch.object(
            module, name, side_effect=AssertionError(f"{name} must not run")
        )
        traps.append(trap.start())
        test.addCleanup(trap.stop)
    return traps


class RollbackSimulationExclusionTests(unittest.TestCase):
    """Task 228."""

    def test_stage_never_invokes_the_rollback_simulation(self) -> None:
        stage = StageRun(self)
        with patch.object(
            eu_server_test,
            "check_eu_firewall_rollback",
            side_effect=AssertionError("rollback simulation must not run"),
        ) as rollback:
            self.assertEqual(stage.run(), 0)

        rollback.assert_not_called()

    def test_every_firewall_replacement_is_the_validated_candidate(self) -> None:
        stage = StageRun(self)
        stage.run()

        candidates = [
            stdin for stdin in stage.runner.stdins
            if isinstance(stdin, str) and "table inet fck_rkn_eu" in stdin
        ]
        self.assertEqual(len(candidates), 1)
        self.assertNotIn("invalid", candidates[0].lower())

    def test_production_sources_do_not_reference_the_rollback_check(self) -> None:
        self.assertNotIn("check_eu_firewall_rollback", production_source())
        self.assertNotIn("firewall-rollback", production_source())


class RepeatedFinalFirewallCheckExclusionTests(unittest.TestCase):
    """Task 230."""

    def test_invasive_firewall_check_never_runs_and_the_eu_policy_is_inspected_once(self) -> None:
        stage = StageRun(self)
        (check,) = trap_attributes(self, eu_server_test, ("check_eu_firewall",))

        self.assertEqual(stage.run(), 0)

        check.assert_not_called()
        inspections = [c for c in stage.runner.commands if "nft list table inet" in c]
        self.assertEqual(len(inspections), 1)
        self.assertNotIn("netns", "\n".join(stage.runner.commands))

    def test_stage_does_not_defer_success_to_ru_actions(self) -> None:
        self.assertNotIn("ru_runner", production_source())
        self.assertNotIn("check_ru_firewall", production_source())


class TestRunStateExclusionTests(unittest.TestCase):
    """Task 231."""

    def test_stage_writes_no_test_run_state(self) -> None:
        stage = StageRun(self)
        traps = trap_attributes(self, last_run, ("save",))
        before = sorted(Path("config/last_run").glob("*")) if Path("config/last_run").exists() else []

        self.assertEqual(stage.run(), 0)

        traps[0].assert_not_called()
        after = sorted(Path("config/last_run").glob("*")) if Path("config/last_run").exists() else []
        self.assertEqual(before, after)

    def test_production_sources_do_not_use_run_state(self) -> None:
        self.assertNotIn("last_run", production_source())


class TestClientLookupExclusionTests(unittest.TestCase):
    """Task 232."""

    def test_stage_never_looks_up_a_test_client_or_queries_gcp(self) -> None:
        stage = StageRun(self)
        (lookup,) = trap_attributes(self, compute, ("lookup_test_client",))

        self.assertEqual(stage.run(), 0)

        lookup.assert_not_called()
        self.assertNotIn("fck_rkn.gcp", production_source())
        self.assertNotIn("gcloud", "\n".join(stage.runner.commands))


class TestClientProvisioningExclusionTests(unittest.TestCase):
    """Task 233."""

    def test_stage_never_provisions_a_test_client(self) -> None:
        stage = StageRun(self)
        (ensure,) = trap_attributes(self, compute, ("ensure_test_client",))

        self.assertEqual(stage.run(), 0)

        ensure.assert_not_called()
        self.assertNotIn("ensure_test_client", production_source())


class TestClientKeyExclusionTests(unittest.TestCase):
    """Task 234."""

    def test_no_test_client_key_is_created(self) -> None:
        stage = StageRun(self)
        with patch.object(
            production_environment, "create_outline_user", wraps=None
        ) as create:
            create.return_value = ACCESS_URL
            self.assertEqual(stage.run(), 0)

        users = [call.args[1] for call in create.call_args_list]
        self.assertNotIn(test_environment.TEST_CLIENT_OUTLINE_USER, users)
        self.assertEqual(len(users), 1)

    def test_stage_runs_no_ru_action(self) -> None:
        self.assertNotIn("TEST_CLIENT_OUTLINE_USER", production_source())
        self.assertNotIn("ru_server_runner", production_source())


class TestClientOutlineConnectionExclusionTests(unittest.TestCase):
    """Task 235."""

    def test_stage_never_invokes_connect_to_outline(self) -> None:
        stage = StageRun(self)
        (trap,) = trap_attributes(self, test_client, ("connect_to_outline",))

        self.assertEqual(stage.run(), 0)

        trap.assert_not_called()
        self.assertNotIn("connect_to_outline", production_source())
        self.assertNotIn("test_client", production_source())


class TestClientOutlineCheckExclusionTests(unittest.TestCase):
    """Task 236."""

    def test_stage_never_invokes_check_outline_connection(self) -> None:
        stage = StageRun(self)
        (trap,) = trap_attributes(self, test_client, ("check_outline_connection",))

        self.assertEqual(stage.run(), 0)

        trap.assert_not_called()
        self.assertNotIn("check_outline_connection", production_source())
        self.assertNotIn("test_client", production_source())


if __name__ == "__main__":
    unittest.main()
