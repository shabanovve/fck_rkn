"""Startup checks executed remotely on the eu-server test VM."""

from __future__ import annotations

import logging
import subprocess
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_CONTAINER_NAME, SQUID_PORT
from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import INSTANCE_NAME


logger = logging.getLogger(__name__)
SQUID_STARTUP_CHECK_COMMAND = (
    f'if [ "$(sudo docker inspect --format=\'{{{{.State.Running}}}}\' '
    f"{SQUID_CONTAINER_NAME})\" != true ]; then "
    "echo 'Squid container is not running' >&2; exit 1; fi"
    " && timeout 5 bash -c "
    f"'echo > /dev/tcp/127.0.0.1/{SQUID_PORT}'"
    " || { echo 'Squid TCP listener is unavailable' >&2; exit 1; }"
)
SSH_COMMAND_TIMEOUT_SECONDS = 90


def check_squid_startup(settings: TestEnvironmentSettings) -> int:
    """Check Squid container health and its local TCP listener over IAP SSH."""
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={SQUID_STARTUP_CHECK_COMMAND}",
        "--quiet",
    ]
    logger.info(
        "Checking Squid container and TCP port %d on %s through IAP",
        SQUID_PORT,
        INSTANCE_NAME,
    )
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
            "Squid startup check over IAP SSH exceeded the %d second timeout",
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
            "Squid startup check or IAP SSH failed with exit status %d",
            result.returncode,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode or 1

    logger.info(
        "Squid container is running and TCP port %d accepts connections",
        SQUID_PORT,
    )
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

    return check_squid_startup(settings)


if __name__ == "__main__":
    raise SystemExit(main())
