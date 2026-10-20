"""Outline Server settings and deployment shared by the EU and RU agents."""

from __future__ import annotations

import logging
import subprocess
import sys

from fck_rkn.config import TestEnvironmentSettings
from fck_rkn.remote import render


logger = logging.getLogger(__name__)
OUTLINE_CONTAINER_NAME = "fck-rkn-outline-shadowbox"
OUTLINE_BASE_IMAGE = "quay.io/outline/shadowbox:stable"
OUTLINE_IMAGE = "fck-rkn-shadowbox:no-public-ip-check"
# The patched outline-ss-server (see remote/scripts/outline_shadowbox.Dockerfile)
# accepts private target addresses so the RU route can reach eu-server's private
# Squid endpoint, in test and production alike.
OUTLINE_SS_SERVER_VERSION = "v1.7.3"
OUTLINE_INSTALLER_URL = (
    "https://raw.githubusercontent.com/Jigsaw-Code/outline-server/"
    "master/src/server_manager/install_scripts/install_server.sh"
)
OUTLINE_STATE_DIRECTORY = "/opt/fck-rkn-outline"
OUTLINE_API_PORT = 10000
OUTLINE_ACCESS_KEY_PORT = 10001
OUTLINE_DEPLOY_TIMEOUT_SECONDS = 900


def deploy_outline_on(settings: TestEnvironmentSettings, instance_name: str) -> int:
    """Install Outline Server on instance_name using its private IP."""
    remote_command = render(
        "deploy_outline.sh",
        container_name=OUTLINE_CONTAINER_NAME,
        image=OUTLINE_IMAGE,
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
        instance_name,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={remote_command}",
        "--quiet",
    ]
    logger.info(
        "Deploying Outline Server on %s over IAP SSH with a private endpoint",
        instance_name,
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

    logger.info("Outline Server deployment completed on %s", instance_name)
    return 0
