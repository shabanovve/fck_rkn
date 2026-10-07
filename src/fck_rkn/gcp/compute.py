"""Compute Engine operations used by the test environment."""

import google.auth
from google.cloud import compute_v1

from fck_rkn.config import TestEnvironmentSettings


def validate_project_access(settings: TestEnvironmentSettings) -> None:
    """Verify ADC credentials can read the configured Compute Engine project."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    compute_v1.ProjectsClient(credentials=credentials).get(
        project=settings.GCP_PROJECT_ID
    )
