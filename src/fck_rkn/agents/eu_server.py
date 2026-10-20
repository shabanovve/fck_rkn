"""Actions executed remotely on the eu-server test VM."""

from __future__ import annotations

import argparse
import logging
import shlex
import sys

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.outline import (  # noqa: F401
    OUTLINE_ACCESS_KEY_PORT,
    OUTLINE_API_PORT,
    OUTLINE_BASE_IMAGE,
    OUTLINE_CONTAINER_NAME,
    OUTLINE_DEPLOY_TIMEOUT_SECONDS,
    OUTLINE_IMAGE,
    OUTLINE_INSTALLER_URL,
    OUTLINE_SS_SERVER_VERSION,
    OUTLINE_STATE_DIRECTORY,
    deploy_outline_on,
)
from fck_rkn.agent_config import eu_server_runner, load_agent_settings
from fck_rkn.remote import render
from fck_rkn.remote.runner import (
    RemoteRunner,
    RemoteStartError,
    RemoteTimeoutError,
    execute,
)


logger = logging.getLogger(__name__)
PING_COMMAND = "ping -c 1 -W 10 youtube.com"
SQUID_CONTAINER_NAME = "fck-rkn-squid"
SQUID_IMAGE = "ubuntu/squid:latest"
SQUID_PORT = 3128
MTPROXY_CONTAINER_NAME = "fck-rkn-mtproxy"
MTPROXY_IMAGE = "telegrammessenger/proxy:latest"
MTPROXY_PORT = 8443
MTPROXY_STATE_DIRECTORY = "/opt/fck-rkn-mtproxy"
OUTLINE_KEY_CREATE_TIMEOUT_SECONDS = 90
SSH_COMMAND_TIMEOUT_SECONDS = 90
SQUID_DEPLOY_TIMEOUT_SECONDS = 900


def run_youtube_ping(runner: RemoteRunner) -> int:
    """Run one bounded ping to youtube.com on eu-server."""
    logger.info("Running remote ping check on eu-server")
    result = execute(
        runner,
        PING_COMMAND,
        "Remote ping",
        SSH_COMMAND_TIMEOUT_SECONDS,
        print_output=True,
        print_stderr=True,
    )
    if result == 0:
        logger.info("Remote ping to youtube.com succeeded")
    return result


def deploy_squid(runner: RemoteRunner) -> int:
    """Install Docker if needed and start Squid on eu-server."""
    remote_command = render(
        "deploy_squid.sh",
        container_name=SQUID_CONTAINER_NAME,
        image=SQUID_IMAGE,
        port=SQUID_PORT,
    )
    logger.info("Deploying Squid on eu-server (TCP port %d)", SQUID_PORT)
    result = execute(
        runner,
        remote_command,
        "Squid deployment",
        SQUID_DEPLOY_TIMEOUT_SECONDS,
        stream_output=True,
    )
    if result == 0:
        logger.info("Squid deployment completed on TCP port %d", SQUID_PORT)
    return result


def deploy_mtproxy(runner: RemoteRunner) -> int:
    """Start MTProxy on eu-server; its secret is generated and kept on the host."""
    remote_command = render(
        "deploy_mtproxy.sh",
        container_name=MTPROXY_CONTAINER_NAME,
        image=MTPROXY_IMAGE,
        port=MTPROXY_PORT,
        state_directory=MTPROXY_STATE_DIRECTORY,
    )
    logger.info("Deploying MTProxy on eu-server (TCP port %d)", MTPROXY_PORT)
    result = execute(
        runner,
        remote_command,
        "MTProxy deployment",
        SQUID_DEPLOY_TIMEOUT_SECONDS,
        stream_output=True,
    )
    if result == 0:
        logger.info("MTProxy deployment completed; the secret is not logged")
    return result


def follow_squid_logs(runner: RemoteRunner) -> int:
    """Stream live Squid container logs from eu-server until interrupted."""
    logger.info(
        "Streaming live Squid container logs from eu-server; press Ctrl-C to stop"
    )
    return execute(
        runner,
        f"sudo docker logs --follow --timestamps {SQUID_CONTAINER_NAME}",
        "Squid log stream",
        None,
        stream_output=True,
    )


def deploy_outline(runner: RemoteRunner, private_address: str) -> int:
    """Install Outline Server on eu-server, bound to its private address."""
    return deploy_outline_on(runner, private_address)


def create_outline_user(
    runner: RemoteRunner,
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
    logger.info("Creating or reusing Outline user %s on eu-server", user_name)
    try:
        result = runner.run(
            remote_command, None, OUTLINE_KEY_CREATE_TIMEOUT_SECONDS
        )
    except RemoteTimeoutError:
        logger.error("Outline user creation timed out")
        return None
    except RemoteStartError as error:
        logger.error("Could not start the remote command runner: %s", error)
        return None

    access_url = result.stdout.strip()
    if (
        result.exit_code != 0
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
            "deploy-mtproxy",
            "deploy-outline",
            "create-outline-user",
        ),
        default="ping",
        help="Action to run (default: ping)",
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
        settings = load_agent_settings()
        runner = eu_server_runner(settings)
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "deploy-squid":
        return deploy_squid(runner)
    if args.action == "deploy-mtproxy":
        return deploy_mtproxy(runner)
    if args.action == "logs-squid":
        return follow_squid_logs(runner)
    if args.action == "deploy-outline":
        return deploy_outline(runner, settings.EU_SERVER_PRIVATE_ADDRESS)
    if args.action == "create-outline-user":
        access_url = create_outline_user(runner, args.name)
        if access_url is None:
            return 1
        print(access_url)
        return 0
    return run_youtube_ping(runner)


if __name__ == "__main__":
    raise SystemExit(main())
