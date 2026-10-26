"""Test-only operations that the production EU stage must never run (tasks 228+)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_production_eu_checks import ScriptedRunner, run_stage

from fck_rkn.agents import eu_server_test
from fck_rkn.orchestration import production_environment

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


if __name__ == "__main__":
    unittest.main()
