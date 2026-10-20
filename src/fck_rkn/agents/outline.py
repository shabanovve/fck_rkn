"""Outline Server settings and deployment shared by the EU and RU agents."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


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

OUTLINE_HEALTH_CHECK_COMMAND = render(
    "outline_health_check.sh",
    container_name=OUTLINE_CONTAINER_NAME,
    image=OUTLINE_IMAGE,
    state_directory=OUTLINE_STATE_DIRECTORY,
    keys_port=OUTLINE_ACCESS_KEY_PORT,
)

OUTLINE_IMAGE_LOAD_TIMEOUT_SECONDS = 900


def load_outline_image(runner: RemoteRunner, archive: Path) -> int:
    """Load the gzip'd image archive on the runner target if it is missing."""
    logger.info("Loading the Outline Server image")
    return execute(
        runner,
        render("load_outline_image.sh", image=OUTLINE_IMAGE),
        "Outline Server image load",
        OUTLINE_IMAGE_LOAD_TIMEOUT_SECONDS,
        stdin=archive,
        stream_output=True,
    )


def deploy_outline_on(runner: RemoteRunner, private_address: str) -> int:
    """Install Outline Server on the runner target, bound to private_address."""
    private_address = private_address.strip()
    if not private_address:
        logger.error("Outline Server private address must not be empty")
        return 1
    remote_command = render(
        "deploy_outline.sh",
        container_name=OUTLINE_CONTAINER_NAME,
        image=OUTLINE_IMAGE,
        base_image=OUTLINE_BASE_IMAGE,
        installer_url=OUTLINE_INSTALLER_URL,
        state_directory=OUTLINE_STATE_DIRECTORY,
        api_port=OUTLINE_API_PORT,
        keys_port=OUTLINE_ACCESS_KEY_PORT,
        private_address=shlex.quote(private_address),
    )
    logger.info("Deploying Outline Server with a private endpoint")
    result = execute(
        runner,
        remote_command,
        "Outline Server deployment",
        OUTLINE_DEPLOY_TIMEOUT_SECONDS,
        stream_output=True,
    )
    if result == 0:
        logger.info("Outline Server deployment completed")
    return result
