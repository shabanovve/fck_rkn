"""Actions executed remotely on the eu-server test VM."""

from __future__ import annotations

import logging
import subprocess
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import INSTANCE_NAME


logger = logging.getLogger(__name__)
PING_COMMAND = "ping -c 1 -W 10 youtube.com"
SSH_COMMAND_TIMEOUT_SECONDS = 90


def run_youtube_ping(settings: TestEnvironmentSettings) -> int:
    """Run one bounded ping to youtube.com on eu-server over IAP SSH."""
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={PING_COMMAND}",
        "--quiet",
    ]
    logger.info("Running remote ping check on %s through IAP", INSTANCE_NAME)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SSH_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Remote ping over IAP SSH exceeded the %d second timeout",
            SSH_COMMAND_TIMEOUT_SECONDS,
        )
        return 1
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        logger.error(
            "Remote ping or IAP SSH failed with exit status %d",
            result.returncode,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode or 1

    logger.info("Remote ping to youtube.com succeeded")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        settings = load_config()
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid test configuration (%s)",
            type(error).__name__,
        )
        return 2

    return run_youtube_ping(settings)


if __name__ == "__main__":
    raise SystemExit(main())
