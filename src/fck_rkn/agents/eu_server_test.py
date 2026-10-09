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
    OUTLINE_IMAGE,
    OUTLINE_STATE_DIRECTORY,
    SQUID_CONTAINER_NAME,
    SQUID_PORT,
)
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
SQUID_HTTP_CHECK_COMMAND = (
    "python3 -c 'import http.client, sys; "
    f'connection = http.client.HTTPConnection("127.0.0.1", {SQUID_PORT}, '
    "timeout=30); "
    'connection.request("GET", "http://youtube.com/", '
    'headers={"Host": "youtube.com"}); '
    "response = connection.getresponse(); "
    "status = response.status; "
    "response.close(); connection.close(); "
    "print(status); "
    "sys.exit(not 200 <= status < 400)'"
)
OUTLINE_HEALTH_CHECK_COMMAND = "\n".join(
    (
        "set -euo pipefail",
        f"container_name={OUTLINE_CONTAINER_NAME}",
        f"expected_image={OUTLINE_IMAGE}",
        'if [ "$(sudo docker inspect --format="{{.Config.Image}}" '
        '"$container_name" 2>/dev/null || true)" != "$expected_image" ] || '
        '[ "$(sudo docker inspect --format="{{.State.Status}}" '
        '"$container_name" 2>/dev/null || true)" != running ]; then',
        '  echo "Outline Server container is not running with the expected image" >&2',
        "  exit 1",
        "fi",
        f'api_url="$(sudo sed -n \'s/^apiUrl://p\' '
        f"{OUTLINE_STATE_DIRECTORY}/access.txt)\"",
        'if [ -z "$api_url" ]; then',
        '  echo "Outline Server API configuration is unavailable" >&2',
        "  exit 1",
        "fi",
        'if ! curl --insecure --fail --silent "$api_url/access-keys" '
        "2>/dev/null | python3 -c 'import json,sys; "
        'data=json.load(sys.stdin); keys=data.get("accessKeys"); '
        "sys.exit(0 if isinstance(keys,list) and keys else 1)'; then",
        '  echo "Outline Server authorized management endpoint is unhealthy" >&2',
        "  exit 1",
        "fi",
        f"if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/"
        f"{OUTLINE_ACCESS_KEY_PORT}'; then",
        '  echo "Outline Server access-key TCP endpoint is unavailable" >&2',
        "  exit 1",
        "fi",
        'echo "Outline Server container, authorized API, and access endpoint are healthy"',
    )
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


def check_outline_health(settings: TestEnvironmentSettings) -> int:
    """Check Outline container, authenticated API, and access port via IAP SSH."""
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={OUTLINE_HEALTH_CHECK_COMMAND}",
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
        return check_outline_health(settings)
    return check_squid_startup(settings)


if __name__ == "__main__":
    raise SystemExit(main())
