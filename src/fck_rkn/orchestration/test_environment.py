"""Command-line entry point for the fck-rkn-test environment orchestrator."""

from __future__ import annotations

import sys

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import validate_project_access


def main() -> int:
    try:
        settings: TestEnvironmentSettings = load_config()
    except (SettingsError, ValidationError) as error:
        print(
            "Configuration error: test settings are missing or invalid.",
            file=sys.stderr,
        )
        if isinstance(error, ValidationError):
            print(error.errors(include_input=False), file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 2

    try:
        validate_project_access(settings)
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
