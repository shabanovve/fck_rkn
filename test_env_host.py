"""Entry point for the fck-rkn-test environment orchestrator."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import google.auth
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import compute_v1
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError


CONFIG_FILE = Path(__file__).resolve().parent / "config" / "test.env"
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


def validate_gcp_access(settings: TestEnvironmentSettings) -> None:
    """Verify ADC credentials can read the configured Compute Engine project."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    compute_v1.ProjectsClient(credentials=credentials).get(
        project=settings.GCP_PROJECT_ID
    )


def main() -> int:
    try:
        settings = load_config()
    except (SettingsError, ValidationError) as error:
        print("Configuration error: test settings are missing or invalid.", file=sys.stderr)
        if isinstance(error, ValidationError):
            print(error.errors(include_input=False), file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 2

    try:
        validate_gcp_access(settings)
    except GoogleAuthError:
        print(
            "GCP authentication failed. Configure Application Default "
            "Credentials with `gcloud auth application-default login`.",
            file=sys.stderr,
        )
        return 3
    except GoogleAPICallError as error:
        print("GCP project access check failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 4
    except ValueError as error:
        print(f"GCP project validation failed: {error}", file=sys.stderr)
        return 4

    print("GCP credentials and test project access are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
