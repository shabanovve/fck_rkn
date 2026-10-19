"""Actions executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import logging
import shlex
import subprocess
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import INSTANCE_NAME
from fck_rkn.remote import render


logger = logging.getLogger(__name__)
PING_COMMAND = "ping -c 1 -W 10 youtube.com"
SQUID_CONTAINER_NAME = "fck-rkn-squid"
SQUID_IMAGE = "ubuntu/squid:latest"
SQUID_PORT = 3128
OUTLINE_CONTAINER_NAME = "fck-rkn-outline-shadowbox"
OUTLINE_BASE_IMAGE = "quay.io/outline/shadowbox:stable"
# Test environment only: the patched outline-ss-server (see
# remote/scripts/outline_shadowbox.Dockerfile) accepts private target addresses
# so the RU route can reach eu-server's private Squid endpoint. Production uses
# the standard image.
OUTLINE_PATCHED_IMAGE = "fck-rkn-shadowbox:no-public-ip-check"
OUTLINE_SS_SERVER_VERSION = "v1.7.3"
OUTLINE_INSTALLER_URL = (
    "https://raw.githubusercontent.com/Jigsaw-Code/outline-server/"
    "master/src/server_manager/install_scripts/install_server.sh"
)
OUTLINE_STATE_DIRECTORY = "/opt/fck-rkn-outline"
OUTLINE_API_PORT = 10000
OUTLINE_ACCESS_KEY_PORT = 10001
OUTLINE_KEY_CREATE_TIMEOUT_SECONDS = 90
SSH_COMMAND_TIMEOUT_SECONDS = 90
SQUID_DEPLOY_TIMEOUT_SECONDS = 900
OUTLINE_DEPLOY_TIMEOUT_SECONDS = 900


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
    remote_command = render(
        "deploy_squid.sh",
        container_name=SQUID_CONTAINER_NAME,
        image=SQUID_IMAGE,
        port=SQUID_PORT,
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

    logger.info("Squid deployment completed on TCP port %d", SQUID_PORT)
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


def outline_image(allow_private_targets: bool) -> str:
    """Return the Outline image: patched for the test environment, else standard."""
    return OUTLINE_PATCHED_IMAGE if allow_private_targets else OUTLINE_BASE_IMAGE


def deploy_outline(
    settings: TestEnvironmentSettings,
    allow_private_targets: bool = False,
) -> int:
    """Install Outline Server using eu-server's private IP.

    The standard stable image is used unless allow_private_targets is set (test
    environment), which rebuilds outline-ss-server to accept private targets.
    """
    remote_command = render(
        "deploy_outline.sh",
        container_name=OUTLINE_CONTAINER_NAME,
        image=outline_image(allow_private_targets),
        patched="true" if allow_private_targets else "false",
        patched_image=OUTLINE_PATCHED_IMAGE,
        base_image=OUTLINE_BASE_IMAGE,
        ss_server_version=OUTLINE_SS_SERVER_VERSION,
        installer_url=OUTLINE_INSTALLER_URL,
        state_directory=OUTLINE_STATE_DIRECTORY,
        api_port=OUTLINE_API_PORT,
        keys_port=OUTLINE_ACCESS_KEY_PORT,
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
        "Deploying Outline Server on %s over IAP SSH with a private endpoint",
        INSTANCE_NAME,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
            timeout=OUTLINE_DEPLOY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Outline Server deployment over IAP SSH exceeded the %d second timeout",
            OUTLINE_DEPLOY_TIMEOUT_SECONDS,
        )
        return 1
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.returncode != 0:
        logger.error(
            "Outline Server deployment or IAP SSH failed with exit status %d",
            result.returncode,
        )
        return result.returncode or 1

    logger.info("Outline Server deployment completed on %s", INSTANCE_NAME)
    return 0


def create_outline_user(
    settings: TestEnvironmentSettings,
    user_name: str,
) -> str | None:
    """Create or reuse a named Outline access key and return its access URL."""
    user_name = user_name.strip()
    if not user_name:
        logger.error("Outline user name must not be empty")
        return None

    remote_command = render(
        "create_outline_user.sh",
        user_name=shlex.quote(user_name),
        state_directory=shlex.quote(OUTLINE_STATE_DIRECTORY),
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
        "Creating or reusing Outline user %s on %s",
        user_name,
        INSTANCE_NAME,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=OUTLINE_KEY_CREATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error("Outline user creation over IAP SSH timed out")
        return None
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return None

    access_url = result.stdout.strip()
    if (
        result.returncode != 0
        or not access_url.startswith("ss://")
        or "\n" in access_url
    ):
        logger.error(
            "Outline user creation failed; sensitive command output was suppressed"
        )
        return None

    logger.info("Outline user key is ready; access URL withheld from logs")
    return access_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run eu-server actions.")
    parser.add_argument(
        "action",
        nargs="?",
        choices=(
            "ping",
            "deploy-squid",
            "logs-squid",
            "deploy-outline",
            "create-outline-user",
        ),
        default="ping",
        help="Action to run (default: ping)",
    )
    parser.add_argument(
        "--allow-private-targets",
        action="store_true",
        help="deploy-outline: rebuild Outline to accept private targets "
        "(test environment only)",
    )
    parser.add_argument(
        "--name",
        help="Display name for the Outline user being created or reused",
    )
    args = parser.parse_args(argv)
    if args.action == "create-outline-user" and not args.name:
        parser.error("--name is required for create-outline-user")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr if args.action == "create-outline-user" else sys.stdout,
    )

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
    if args.action == "deploy-outline":
        return deploy_outline(settings, args.allow_private_targets)
    if args.action == "create-outline-user":
        access_url = create_outline_user(settings, args.name)
        if access_url is None:
            return 1
        print(access_url)
        return 0
    return run_youtube_ping(settings)


if __name__ == "__main__":
    raise SystemExit(main())
