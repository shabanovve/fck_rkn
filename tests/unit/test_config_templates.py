from __future__ import annotations

import unittest
from pathlib import Path

from fck_rkn.agent_config import AgentSettings
from fck_rkn.config import TestEnvironmentSettings

CONFIG = Path(__file__).resolve().parents[2] / "config"
TEMPLATES = (
    ("test.env.example", TestEnvironmentSettings),
    ("agents.env.example", AgentSettings),
)


def variables(template: str) -> list[tuple[str, list[str]]]:
    """Return each variable of a template with the comment lines above it."""
    result: list[tuple[str, list[str]]] = []
    comment: list[str] = []
    for line in (CONFIG / template).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            comment.append(line)
        elif "=" in line:
            result.append((line.split("=", 1)[0], comment))
            comment = []
        else:
            comment = []
    return result


class ConfigTemplateTests(unittest.TestCase):
    def test_every_variable_has_a_detailed_comment(self) -> None:
        for template, _ in TEMPLATES:
            for name, comment in variables(template):
                with self.subTest(template=template, variable=name):
                    self.assertGreaterEqual(len(" ".join(comment)), 40)

    def test_templates_match_the_settings_the_code_reads(self) -> None:
        for template, settings in TEMPLATES:
            with self.subTest(template=template):
                self.assertEqual(
                    {name for name, _ in variables(template)},
                    set(settings.model_fields),
                )


if __name__ == "__main__":
    unittest.main()
