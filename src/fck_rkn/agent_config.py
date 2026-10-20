"""Platform-neutral settings for server agents (no cloud provider fields)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from fck_rkn.remote.runner import RemoteRunner
from fck_rkn.remote.ssh import SshRunner

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG_FILE = REPOSITORY_ROOT / "config" / "agents.env"

logger = logging.getLogger(__name__)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=AGENT_CONFIG_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    EU_SERVER_HOST: str = Field(min_length=1)
    RU_SERVER_HOST: str = Field(min_length=1)
    EU_SERVER_PRIVATE_ADDRESS: str = Field(min_length=1)
    RU_SERVER_PRIVATE_ADDRESS: str = Field(min_length=1)
    SSH_USER: str = Field(min_length=1)
    SSH_KEY_PATH: Path
    SSH_PORT: int = Field(default=22, ge=1, le=65535)


def load_agent_settings(path: Path | None = None) -> AgentSettings:
    config_path = AGENT_CONFIG_FILE if path is None else path
    logger.info("Loading agent configuration from %s", config_path)
    return AgentSettings(_env_file=config_path)


def eu_server_runner(settings: AgentSettings) -> RemoteRunner:
    return _ssh_runner(settings, settings.EU_SERVER_HOST)


def ru_server_runner(settings: AgentSettings) -> RemoteRunner:
    return _ssh_runner(settings, settings.RU_SERVER_HOST)


def _ssh_runner(settings: AgentSettings, host: str) -> SshRunner:
    return SshRunner(
        host=host,
        user=settings.SSH_USER,
        key_path=settings.SSH_KEY_PATH,
        port=settings.SSH_PORT,
    )
