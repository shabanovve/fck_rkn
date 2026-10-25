import ast
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from fck_rkn.agent_config import (
    AgentSettings,
    eu_server_runner,
    load_agent_settings,
    ru_server_runner,
)
from fck_rkn.remote.ssh import SshRunner

AGENT_CONFIG = Path(__file__).resolve().parents[2] / "src" / "fck_rkn" / "agent_config.py"


def write_env(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "agents.env"
    path.write_text(text, encoding="utf-8")
    return path


VALID = (
    "EU_SERVER_ADDRESS=192.0.2.10\nRU_SERVER_HOST=ru.example\n"
    "RU_SERVER_PRIVATE_ADDRESS=ru.internal\n"
    "SSH_USER=deploy\nSSH_KEY_PATH=/keys/id\n"
)


class AgentConfigTests(unittest.TestCase):
    def test_loads_with_default_port(self) -> None:
        settings = load_agent_settings(write_env(VALID))
        self.assertEqual(settings.SSH_PORT, 22)
        self.assertEqual(settings.SSH_KEY_PATH, Path("/keys/id"))

    def test_runners_target_their_own_hosts(self) -> None:
        settings = load_agent_settings(write_env(VALID + "SSH_PORT=2222\n"))
        eu = eu_server_runner(settings)
        ru = ru_server_runner(settings)
        self.assertIsInstance(eu, SshRunner)
        self.assertEqual((eu.host, ru.host), ("192.0.2.10", "ru.example"))
        self.assertEqual(eu.port, 2222)
        self.assertEqual(ru.user, "deploy")

    def test_missing_host_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            load_agent_settings(write_env("SSH_USER=a\nSSH_KEY_PATH=/k\n"))

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            load_agent_settings(write_env(VALID + "SSH_PORT=70000\n"))

    def test_module_has_no_cloud_dependency(self) -> None:
        tree = ast.parse(AGENT_CONFIG.read_text(encoding="utf-8"))
        imported = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse([m for m in imported if "gcp" in m or m == "fck_rkn.config"])
        self.assertNotIn("GCP_", AGENT_CONFIG.read_text(encoding="utf-8"))
        self.assertTrue(issubclass(AgentSettings, object))


if __name__ == "__main__":
    unittest.main()
