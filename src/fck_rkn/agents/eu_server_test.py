"""Startup checks executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import (
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_STATE_DIRECTORY,
    SQUID_CONTAINER_NAME,
    SQUID_PORT,
    outline_image,
)
from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import INSTANCE_NAME
from fck_rkn.remote import render


logger = logging.getLogger(__name__)
SSH_COMMAND_TIMEOUT_SECONDS = 90
SQUID_STARTUP_CHECK_COMMAND = render(
    "squid_startup_check.sh",
    container_name=SQUID_CONTAINER_NAME,
    port=SQUID_PORT,
)
SQUID_HTTP_CHECK_COMMAND = render("squid_http_check.sh", port=SQUID_PORT)


def build_outline_health_check_command(allow_private_targets: bool) -> str:
    return render(
        "outline_health_check.sh",
        container_name=OUTLINE_CONTAINER_NAME,
        image=outline_image(allow_private_targets),
        state_directory=OUTLINE_STATE_DIRECTORY,
        keys_port=OUTLINE_ACCESS_KEY_PORT,
    )


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


def check_squid_http(settings: TestEnvironmentSettings) -> int:
    """Check an HTTP request to youtube.com through Squid on eu-server."""
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={SQUID_HTTP_CHECK_COMMAND}",
        "--quiet",
    ]
    logger.info(
        "Checking HTTP GET youtube.com through Squid on %s over IAP",
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
            "Squid HTTP check over IAP SSH exceeded the %d second timeout",
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
            "HTTP request through Squid or IAP SSH failed with exit status %d",
            result.returncode,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode or 1

    logger.info("HTTP GET youtube.com through Squid succeeded")
    return 0


def check_outline_health(
    settings: TestEnvironmentSettings,
    allow_private_targets: bool = False,
) -> int:
    """Check Outline container, authenticated API, and access port via IAP SSH."""
    health_check_command = build_outline_health_check_command(allow_private_targets)
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={health_check_command}",
        "--quiet",
    ]
    logger.info(
        "Checking Outline Server container, authorized API, and TCP access "
        "port %d on %s through IAP",
        OUTLINE_ACCESS_KEY_PORT,
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
            "Outline Server health check over IAP SSH exceeded the %d second timeout",
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
            "Outline Server health check or IAP SSH failed with exit status %d",
            result.returncode,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode or 1

    logger.info("Outline Server authorized endpoint is healthy")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(
        description="Run eu-server service checks."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("startup", "http", "outline"),
        default="startup",
        help="Check to run (default: startup)",
    )
    parser.add_argument(
        "--allow-private-targets",
        action="store_true",
        help="outline: expect the patched test-environment Outline image",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_config()
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid test configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "http":
        return check_squid_http(settings)
    if args.action == "outline":
        return check_outline_health(settings, args.allow_private_targets)
    return check_squid_startup(settings)


if __name__ == "__main__":
    raise SystemExit(main())
