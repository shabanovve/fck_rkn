"""Actions executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import sys
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import MTPROXY_PORT, SQUID_PORT
from fck_rkn.agents.outline import deploy_outline_on
from fck_rkn.agent_config import load_agent_settings, ru_server_runner
from fck_rkn.agents.ru_firewall import build_ru_firewall_rules
from fck_rkn.remote import render
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
PROBE_CONTAINER_NAME = "fck-rkn-outline-probe"
PROBE_IMAGE = "shadowsocks/shadowsocks-libev:latest"
PROBE_LOCAL_PORT = 13128
PROBE_TARGET_URL = "http://youtube.com/"
PROBE_TIMEOUT_SECONDS = 600
CLIENT_CONTAINER_NAME = "fck-rkn-outline-client"
CLIENT_IMAGE = PROBE_IMAGE
CLIENT_STATE_DIRECTORY = "/opt/fck-rkn-outline-client"
CLIENT_LOCAL_PORT = 1080
CLIENT_DEPLOY_TIMEOUT_SECONDS = 900
FORWARD_CONTAINER_NAME = "fck-rkn-squid-forward"
FORWARD_STATE_DIRECTORY = "/opt/fck-rkn-squid-forward"
FORWARD_LOCAL_PORT = SQUID_PORT
MTPROXY_FORWARD_CONTAINER_NAME = "fck-rkn-mtproxy-forward"
MTPROXY_FORWARD_STATE_DIRECTORY = "/opt/fck-rkn-mtproxy-forward"
MTPROXY_FORWARD_LOCAL_PORT = MTPROXY_PORT
RECOVERY_ATTEMPTS = 5
RECOVERY_DELAY_SECONDS = 10
RECOVERY_TIMEOUT_SECONDS = 600
FIREWALL_DEPLOY_TIMEOUT_SECONDS = 600
FIREWALL_RULES_FILE = "/etc/fck-rkn/ru-firewall.nft"
FIREWALL_UNIT_FILE = "/etc/systemd/system/fck-rkn-ru-firewall.service"
FIREWALL_LOCK_FILE = "/run/lock/fck-rkn-ru-firewall.lock"


def parse_access_url(access_url: str) -> tuple[str, str, str, int]:
    """Return (method, password, host, port) from an Outline ss:// access URL."""
    parts = urlsplit(access_url.strip())
    if parts.scheme != "ss" or not parts.hostname or not parts.port:
        raise ValueError("Outline access URL is not a valid ss:// URL")
    if parts.username is None:
        raise ValueError("Outline access URL has no credentials")
    userinfo = unquote(parts.username)
    try:
        padded = userinfo + "=" * (-len(userinfo) % 4)
        method, password = (
            base64.urlsafe_b64decode(padded).decode("utf-8").split(":", 1)
        )
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ValueError("Outline access URL credentials are malformed") from error
    if not method or not password:
        raise ValueError("Outline access URL credentials are incomplete")
    return method, password, parts.hostname, parts.port


def run_remote_with_input(
    runner: RemoteRunner,
    remote_command: str,
    stdin_text: str,
    description: str,
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> int :
    """Run remote_command on ru-server, passing secrets only through stdin."""
    logger.info("%s on ru-server", description)
    return execute(
        runner,
        remote_command,
        description,
        timeout,
        stdin=stdin_text,
        print_output=True,
    )


def run_remote_with_key(
    runner: RemoteRunner,
    access_url: str,
    remote_command: str,
    description: str,
) -> int:
    """Run remote_command on ru-server, passing the access key through stdin."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    return run_remote_with_input(
        runner,
        remote_command,
        f"{method} {password} {host} {port}\n",
        description,
    )


def build_firewall_apply_command() -> str:
    return render(
        "apply_ru_firewall.sh",
        rules_file=FIREWALL_RULES_FILE,
        unit_file=FIREWALL_UNIT_FILE,
        lock_file=FIREWALL_LOCK_FILE,
    )


def apply_ru_firewall(runner: RemoteRunner, config_json: str) -> int:
    """Validate and transactionally install the RU traffic policy."""
    try:
        rules = build_ru_firewall_rules(config_json)
    except ValueError as error:
        logger.error("Invalid RU firewall configuration: %s", error)
        return 1
    return run_remote_with_input(
        runner,
        build_firewall_apply_command(),
        rules,
        "Applying the RU guest firewall policy",
        timeout=FIREWALL_DEPLOY_TIMEOUT_SECONDS,
    )


def build_client_deploy_command() -> str:
    """Return a remote script that starts the persistent Outline client.

    The client configuration (including the key) arrives on stdin and is stored
    in a root-only file; it is never part of the command line.
    """
    return _build_container_deploy_command(
        CLIENT_CONTAINER_NAME, CLIENT_STATE_DIRECTORY, "ss-local", "Outline client"
    )


def build_forward_deploy_command() -> str:
    """Return a remote script that starts the persistent EU Squid TCP forward."""
    return _build_container_deploy_command(
        FORWARD_CONTAINER_NAME, FORWARD_STATE_DIRECTORY, "ss-tunnel", "Squid forward"
    )


def build_mtproxy_forward_deploy_command() -> str:
    """Return a remote script that starts the persistent EU MTProxy TCP forward."""
    return _build_container_deploy_command(
        MTPROXY_FORWARD_CONTAINER_NAME,
        MTPROXY_FORWARD_STATE_DIRECTORY,
        "ss-tunnel",
        "MTProxy forward",
    )


def _build_container_deploy_command(
    container_name: str, state_directory: str, entrypoint: str, title: str
) -> str:
    return render(
        "deploy_container.sh",
        container_name=container_name,
        image=CLIENT_IMAGE,
        state_directory=state_directory,
        entrypoint=entrypoint,
        title=title,
    )


def build_client_config(method: str, password: str, host: str, port: int) -> str:
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": CLIENT_LOCAL_PORT,
        }
    )


def build_forward_config(method: str, password: str, host: str, port: int) -> str:
    """Return the ss-tunnel config forwarding RU localhost to EU Squid via Outline."""
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": FORWARD_LOCAL_PORT,
            "tunnel_address": f"{host}:{SQUID_PORT}",
        }
    )


def build_mtproxy_forward_config(
    method: str, password: str, host: str, port: int
) -> str:
    """Return the ss-tunnel config forwarding RU localhost to EU MTProxy via Outline."""
    return json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": MTPROXY_FORWARD_LOCAL_PORT,
            "tunnel_address": f"{host}:{MTPROXY_PORT}",
        }
    )


def deploy_mtproxy_forward(runner: RemoteRunner, access_url: str) -> int:
    """Forward ru-server localhost TCP/8443 to EU MTProxy through EU Outline."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    result = run_remote_with_input(
        runner,
        build_mtproxy_forward_deploy_command(),
        build_mtproxy_forward_config(method, password, host, port),
        "Deploying the EU MTProxy TCP forward in Docker",
        timeout=CLIENT_DEPLOY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("EU MTProxy forward deployment completed on ru-server")
    return result


def deploy_squid_forward(
    runner: RemoteRunner,
    access_url: str,
) -> int:
    """Forward ru-server localhost TCP/3128 to EU Squid through EU Outline."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    result = run_remote_with_input(
        runner,
        build_forward_deploy_command(),
        build_forward_config(method, password, host, port),
        "Deploying the EU Squid TCP forward in Docker",
        timeout=CLIENT_DEPLOY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("EU Squid forward deployment completed on %s", "ru-server")
    return result


def deploy_outline_client(
    runner: RemoteRunner,
    access_url: str,
) -> int:
    """Run the Outline client (ss-local) in Docker on ru-server using the test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    client_config = build_client_config(method, password, host, port)
    result = run_remote_with_input(
        runner,
        build_client_deploy_command(),
        client_config,
        "Deploying the Outline client in Docker",
        timeout=CLIENT_DEPLOY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("Outline client deployment completed on %s", "ru-server")
    return result


def build_recovery_command() -> str:
    return render(
        "recover_tunnel.sh",
        attempts=RECOVERY_ATTEMPTS,
        delay_seconds=RECOVERY_DELAY_SECONDS,
        local_port=FORWARD_LOCAL_PORT,
        target_url=PROBE_TARGET_URL,
        client_container_name=CLIENT_CONTAINER_NAME,
        forward_container_name=FORWARD_CONTAINER_NAME,
    )


def recover_tunnel(runner: RemoteRunner) -> int:
    """Restore the Outline tunnel with bounded retries, never bypassing it."""
    result = run_remote_with_input(
        runner,
        build_recovery_command(),
        "",
        "Recovering the Outline tunnel (client traffic stays blocked on failure)",
        timeout=RECOVERY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("Outline tunnel is up on %s", "ru-server")
    return result


def recover_mtproxy_forward(runner: RemoteRunner) -> int:
    """Restart the MTProxy forward with bounded retries, never bypassing it."""
    result = run_remote_with_input(
        runner,
        render(
            "recover_mtproxy_forward.sh",
            container_name=MTPROXY_FORWARD_CONTAINER_NAME,
            local_port=MTPROXY_FORWARD_LOCAL_PORT,
            attempts=RECOVERY_ATTEMPTS,
            delay_seconds=RECOVERY_DELAY_SECONDS,
        ),
        "",
        "Recovering the MTProxy forward (traffic stays blocked on failure)",
        timeout=RECOVERY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("MTProxy forward is up on %s", "ru-server")
    return result


def stop_mtproxy_forward(runner: RemoteRunner) -> int:
    """Simulate tunnel loss by stopping the MTProxy forward container."""
    return run_remote_with_input(
        runner,
        render("stop_tunnel.sh", container_name=MTPROXY_FORWARD_CONTAINER_NAME),
        "",
        "Simulating tunnel loss by stopping the MTProxy forward",
    )


def deploy_outline_server(runner: RemoteRunner, private_address: str) -> int:
    """Run the approved Outline Server container on ru-server (private address).

    Uses the same patched image and installer as eu-server; the existing client
    (1080) and forward (3128) listeners do not conflict with ports 10000/10001.
    """
    return deploy_outline_on(runner, private_address)


def stop_tunnel(runner: RemoteRunner) -> int:
    """Simulate tunnel loss by stopping the forward container."""
    result = run_remote_with_input(
        runner,
        render("stop_tunnel.sh", container_name=FORWARD_CONTAINER_NAME),
        "",
        "Simulating tunnel loss by stopping the Squid forward",
    )
    if result == 0:
        logger.info("Outline tunnel stopped on %s", "ru-server")
    return result


def run_outline_squid_probe(
    runner: RemoteRunner,
    access_url: str,
) -> int:
    """Try to reach the EU Squid endpoint from ru-server through EU Outline."""
    remote_command = render(
        "outline_squid_probe.sh",
        container_name=PROBE_CONTAINER_NAME,
        image=PROBE_IMAGE,
        local_port=PROBE_LOCAL_PORT,
        squid_port=SQUID_PORT,
        target_url=PROBE_TARGET_URL,
    )
    result = run_remote_with_key(
        runner,
        access_url,
        remote_command,
        f"Probing EU Squid TCP/{SQUID_PORT} through the Outline tunnel",
    )
    if result == 0:
        logger.info("EU Squid is reachable from ru-server through Outline")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server actions.")
    parser.add_argument(
        "action",
        choices=(
            "probe-outline-squid",
            "deploy-outline-client",
            "deploy-squid-forward",
            "deploy-mtproxy-forward",
            "recover-tunnel",
            "stop-tunnel",
            "recover-mtproxy-forward",
            "stop-mtproxy-forward",
            "deploy-outline",
            "apply-firewall",
        ),
        help="Action to run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        settings = load_agent_settings()
        runner = ru_server_runner(settings)
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid agent configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "probe-outline-squid":
        # The key is read from stdin so it never appears in argv or shell history.
        return run_outline_squid_probe(runner, sys.stdin.readline())
    if args.action == "deploy-outline-client":
        return deploy_outline_client(runner, sys.stdin.readline())
    if args.action == "deploy-squid-forward":
        return deploy_squid_forward(runner, sys.stdin.readline())
    if args.action == "deploy-mtproxy-forward":
        return deploy_mtproxy_forward(runner, sys.stdin.readline())
    if args.action == "recover-tunnel":
        return recover_tunnel(runner)
    if args.action == "deploy-outline":
        return deploy_outline_server(runner, settings.RU_SERVER_PRIVATE_ADDRESS)
    if args.action == "apply-firewall":
        return apply_ru_firewall(runner, sys.stdin.read())
    if args.action == "recover-mtproxy-forward":
        return recover_mtproxy_forward(runner)
    if args.action == "stop-mtproxy-forward":
        return stop_mtproxy_forward(runner)
    if args.action == "stop-tunnel":
        return stop_tunnel(runner)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
