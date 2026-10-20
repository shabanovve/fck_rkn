"""Outline Server settings and deployment shared by the EU and RU agents."""

from __future__ import annotations

import logging

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


def deploy_outline_on(runner: RemoteRunner) -> int:
    """Install Outline Server on the runner target using its private IP."""
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
