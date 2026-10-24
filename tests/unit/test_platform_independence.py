import re
import unittest
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[2] / "src" / "fck_rkn" / "agents"
MIGRATED = tuple(p.name for p in AGENTS.glob("*.py") if p.name != "__init__.py")
FORBIDDEN = re.compile(r"gcloud|\bIAP\b|iap|fck_rkn\.gcp|TestEnvironmentSettings|metadata\.google|(?i:google)")


class PlatformIndependenceTests(unittest.TestCase):
    def test_migrated_agents_do_not_reference_gcp(self) -> None:
        for name in MIGRATED:
            with self.subTest(module=name):
                text = (AGENTS / name).read_text(encoding="utf-8")
                self.assertIsNone(FORBIDDEN.search(text))
