"""Actions executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import INSTANCE_NAME


logger = logging.getLogger(__name__)
PING_COMMAND = "ping -c 1 -W 10 youtube.com"
SQUID_CONTAINER_NAME = "fck-rkn-squid"
SQUID_IMAGE = "ubuntu/squid:latest"
SQUID_PORT = 3128
SSH_COMMAND_TIMEOUT_SECONDS = 90
SQUID_DEPLOY_TIMEOUT_SECONDS = 900


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


def deploy_squid(settings: TestEnvironmentSettings) -> int:
    """Install Docker if needed and start Squid on eu-server over IAP SSH."""
    remote_command = " && ".join(
        (
            "command -v docker >/dev/null 2>&1 || "
            "(sudo apt-get update && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io)",
            f"sudo docker pull {SQUID_IMAGE}",
            f"if sudo docker container inspect {SQUID_CONTAINER_NAME} "
            ">/dev/null 2>&1; then "
            f"sudo docker rm -f {SQUID_CONTAINER_NAME}; fi",
            "sudo docker run --detach "
            f"--name {SQUID_CONTAINER_NAME} "
            "--restart unless-stopped "
            f"--publish {SQUID_PORT}:{SQUID_PORT}/tcp "
            f"{SQUID_IMAGE}",
        )
    )
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={remote_command}",
        "--quiet",
    ]
    logger.info(
        "Deploying Squid on %s through IAP SSH (TCP port %d)",
        INSTANCE_NAME,
        SQUID_PORT,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
            timeout=SQUID_DEPLOY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Squid deployment over IAP SSH exceeded the %d second timeout",
            SQUID_DEPLOY_TIMEOUT_SECONDS,
        )
        return 1
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.returncode != 0:
        logger.error(
            "Squid deployment or IAP SSH failed with exit status %d",
            result.returncode,
        )
        return result.returncode or 1

    logger.info("Squid container started on TCP port %d", SQUID_PORT)
    return 0


def follow_squid_logs(settings: TestEnvironmentSettings) -> int:
    """Stream live Squid container logs from eu-server through IAP SSH."""
    remote_command = (
        f"sudo docker logs --follow --timestamps {SQUID_CONTAINER_NAME}"
    )
    command = [
        "gcloud",
        "compute",
        "ssh",
        INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={remote_command}",
        "--quiet",
    ]
    logger.info(
        "Streaming live Squid container logs from %s through IAP; "
        "press Ctrl-C to stop",
        INSTANCE_NAME,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.returncode != 0:
        logger.error(
            "Squid log stream or IAP SSH failed with exit status %d",
            result.returncode,
        )
        return result.returncode or 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="Run eu-server test actions.")
    parser.add_argument(
        "action",
        nargs="?",
        choices=("ping", "deploy-squid", "logs-squid"),
        default="ping",
        help="Action to run (default: ping)",
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

    if args.action == "deploy-squid":
        return deploy_squid(settings)
    if args.action == "logs-squid":
        return follow_squid_logs(settings)
    return run_youtube_ping(settings)


if __name__ == "__main__":
    raise SystemExit(main())
