"""Inputs of the latest test_env_host run, kept so one check can be repeated."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fck_rkn.agent_config import REPOSITORY_ROOT


logger = logging.getLogger(__name__)
LAST_RUN_DIRECTORY = REPOSITORY_ROOT / "config" / "last_run"
EU_FIREWALL_CONFIG = "eu-firewall.json"
RU_FIREWALL_CONFIG = "ru-firewall.json"
CRAWLER_SOURCE_CONFIG = "crawler-source.json"
RU_PROBE_KEY = "ru-probe-key.txt"
TEST_CLIENT_KEY = "test-client-key.txt"


def save(name: str, content: str, directory: Path = LAST_RUN_DIRECTORY) -> None:
    """Store an input readable by the owner only; a failure only logs a warning."""
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as error:
        logger.warning("Could not save the last-run input %s: %s", name, error)


def load(name: str, directory: Path = LAST_RUN_DIRECTORY) -> str:
    """Read an input saved by the latest test_env_host run."""
    try:
        return (directory / name).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(
            f"No saved input {name}; run test-env-host first ({error.strerror})"
        ) from error
