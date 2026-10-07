"""Configuration loading and validation for the test environment."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPOSITORY_ROOT / "config" / "test.env"
GCP_PROJECT_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")


class TestEnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=CONFIG_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    GCP_PROJECT_ID: str = Field(min_length=6, max_length=30)

    @field_validator("GCP_PROJECT_ID")
    @classmethod
    def validate_gcp_project_id(cls, value: str) -> str:
        if not GCP_PROJECT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "must be 6-30 lowercase letters, digits, or hyphens, "
                "start with a letter, and end with a letter or digit"
            )
        return value


def load_config(path: Path | None = None) -> TestEnvironmentSettings:
    """Load and validate the test configuration before cloud API operations."""
    config_path = CONFIG_FILE if path is None else path
    return TestEnvironmentSettings(_env_file=config_path)
