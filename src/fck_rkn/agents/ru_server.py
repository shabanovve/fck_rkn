"""Actions executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import SQUID_PORT
from fck_rkn.config import TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import RU_INSTANCE_NAME


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


def build_tunnel_command(probe_lines: tuple[str, ...]) -> str:
    """Return a remote script that opens the Outline tunnel, then runs probe_lines.

    The script reads "method password host port" from stdin.
    """
    return "\n".join(
        (
            "set -euo pipefail",
            "read -r method password host port",
            "if ! command -v docker >/dev/null 2>&1 || "
            "! command -v curl >/dev/null 2>&1; then",
            "  sudo apt-get update",
            "  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io curl",
            "fi",
            "sudo systemctl enable --now docker",
            f"sudo docker rm -f {PROBE_CONTAINER_NAME} >/dev/null 2>&1 || true",
            f"sudo docker pull {PROBE_IMAGE} >/dev/null",
            f"trap 'sudo docker rm -f {PROBE_CONTAINER_NAME} >/dev/null 2>&1 || true' EXIT",
            "sudo docker run --detach "
            f"--name {PROBE_CONTAINER_NAME} --network host "
            "--entrypoint ss-tunnel "
            f"{PROBE_IMAGE} "
            '-s "$host" -p "$port" -k "$password" -m "$method" '
            f'-l {PROBE_LOCAL_PORT} -L "$host:{SQUID_PORT}" -v >/dev/null',
            "sleep 3",
            "diagnose() {",
            '  echo "--- ss-tunnel container state and logs ---"',
            f"  sudo docker ps -a --filter name={PROBE_CONTAINER_NAME} "
            "--format '{{.Status}}'",
            f"  sudo docker logs --tail 30 {PROBE_CONTAINER_NAME} 2>&1 | "
            "while IFS= read -r line; do echo \"${line//\"$password\"/[redacted]}\"; done",
            "}",
            *probe_lines,
        )
    )


def run_remote_with_input(
    settings: TestEnvironmentSettings,
    remote_command: str,
    stdin_text: str,
    description: str,
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> int:
    """Run remote_command on ru-server, passing secrets only through stdin."""
    command = [
        "gcloud",
        "compute",
        "ssh",
        RU_INSTANCE_NAME,
        f"--project={settings.GCP_PROJECT_ID}",
        f"--zone={settings.GCP_ZONE}",
        "--tunnel-through-iap",
        f"--command={remote_command}",
        "--quiet",
    ]
    logger.info("%s on %s", description, RU_INSTANCE_NAME)
    try:
        result = subprocess.run(
            command,
            input=stdin_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("%s exceeded the %d second timeout", description, timeout)
        return 1
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        logger.error(
            "%s failed with exit status %d; sensitive command output was suppressed",
            description,
            result.returncode,
        )
        return result.returncode or 1
    return 0


def run_remote_with_key(
    settings: TestEnvironmentSettings,
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
        settings,
        remote_command,
        f"{method} {password} {host} {port}\n",
        description,
    )


def build_client_deploy_command() -> str:
    """Return a remote script that starts the persistent Outline client.

    The client configuration (including the key) arrives on stdin and is stored
    in a root-only file; it is never part of the command line.
    """
    return "\n".join(
        (
            "set -euo pipefail",
            "config=$(cat)",
            f"container_name={CLIENT_CONTAINER_NAME}",
            f"expected_image={CLIENT_IMAGE}",
            "if ! command -v docker >/dev/null 2>&1; then",
            "  sudo apt-get update",
            "  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io",
            "fi",
            "sudo systemctl enable --now docker",
            'if sudo docker container inspect "$container_name" >/dev/null 2>&1; then',
            '  actual_image="$(sudo docker inspect --format="{{.Config.Image}}" "$container_name")"',
            '  actual_state="$(sudo docker inspect --format="{{.State.Status}}" "$container_name")"',
            '  if [ "$actual_image" = "$expected_image" ] && [ "$actual_state" = running ]; then',
            '    echo "Outline client container is already running; leaving it unchanged"',
            "    exit 0",
            "  fi",
            '  echo "Existing Outline client container differs; refusing to replace it" >&2',
            "  exit 1",
            "fi",
            f"sudo install -d -m 700 {CLIENT_STATE_DIRECTORY}",
            f"printf '%s' \"$config\" | sudo sh -c 'umask 077; cat > {CLIENT_STATE_DIRECTORY}/config.json'",
            'sudo docker pull "$expected_image" >/dev/null',
            "sudo docker run --detach "
            '--name "$container_name" '
            "--restart unless-stopped --network host "
            f"--volume {CLIENT_STATE_DIRECTORY}:/etc/shadowsocks:ro "
            "--entrypoint ss-local "
            '"$expected_image" -c /etc/shadowsocks/config.json >/dev/null',
            'echo "Outline client container started"',
        )
    )


def deploy_outline_client(
    settings: TestEnvironmentSettings,
    access_url: str,
) -> int:
    """Run the Outline client (ss-local) in Docker on ru-server using the test key."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1
    client_config = json.dumps(
        {
            "server": host,
            "server_port": port,
            "method": method,
            "password": password,
            "local_address": "127.0.0.1",
            "local_port": CLIENT_LOCAL_PORT,
        }
    )
    result = run_remote_with_input(
        settings,
        build_client_deploy_command(),
        client_config,
        "Deploying the Outline client in Docker",
        timeout=CLIENT_DEPLOY_TIMEOUT_SECONDS,
    )
    if result == 0:
        logger.info("Outline client deployment completed on %s", RU_INSTANCE_NAME)
    return result


def run_outline_squid_probe(
    settings: TestEnvironmentSettings,
    access_url: str,
) -> int:
    """Try to reach the EU Squid endpoint from ru-server through EU Outline."""
    remote_command = build_tunnel_command(
        (
            "curl_err=$(mktemp)",
            "if ! status=$(curl --verbose --silent --output /dev/null "
            "--max-time 30 --write-out '%{http_code}' "
            f"--proxy http://127.0.0.1:{PROBE_LOCAL_PORT} {PROBE_TARGET_URL} "
            '2>"$curl_err"); then',
            '  echo "--- curl output ---"',
            '  cat "$curl_err"',
            "  diagnose",
            "  exit 1",
            "fi",
            'echo "EU Squid answered through the Outline tunnel with HTTP $status"',
        )
    )
    result = run_remote_with_key(
        settings,
        access_url,
        remote_command,
        f"Probing EU Squid TCP/{SQUID_PORT} through the Outline tunnel",
    )
    if result == 0:
        logger.info("EU Squid is reachable from %s through Outline", RU_INSTANCE_NAME)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server actions.")
    parser.add_argument(
        "action",
        choices=("probe-outline-squid", "deploy-outline-client"),
        help="Action to run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        settings = load_config()
    except (OSError, SettingsError, ValidationError, ValueError) as error:
        logger.error(
            "Could not load valid test configuration (%s)",
            type(error).__name__,
        )
        return 2

    if args.action == "probe-outline-squid":
        # The key is read from stdin so it never appears in argv or shell history.
        return run_outline_squid_probe(settings, sys.stdin.readline())
    if args.action == "deploy-outline-client":
        return deploy_outline_client(settings, sys.stdin.readline())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
