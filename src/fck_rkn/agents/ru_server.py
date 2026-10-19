"""Actions executed remotely on the ru-server test VM."""

from __future__ import annotations

import argparse
import base64
import binascii
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
PROBE_TARGET_URL = "http://example.com/"
PROBE_TIMEOUT_SECONDS = 600


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


def run_outline_squid_probe(
    settings: TestEnvironmentSettings,
    access_url: str,
) -> int:
    """Try to reach the EU Squid endpoint from ru-server through EU Outline."""
    try:
        method, password, host, port = parse_access_url(access_url)
    except ValueError as error:
        logger.error("%s", error)
        return 1

    remote_command = "\n".join(
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
            f'-l {PROBE_LOCAL_PORT} -L "$host:{SQUID_PORT}" >/dev/null',
            "sleep 3",
            "status=$(curl --silent --show-error --output /dev/null "
            "--max-time 30 --write-out '%{http_code}' "
            f"--proxy http://127.0.0.1:{PROBE_LOCAL_PORT} {PROBE_TARGET_URL})",
            'echo "EU Squid answered through the Outline tunnel with HTTP $status"',
            '[ "$status" != 000 ]',
        )
    )
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
    logger.info(
        "Probing EU Squid TCP/%d from %s through the Outline tunnel",
        SQUID_PORT,
        RU_INSTANCE_NAME,
    )
    try:
        result = subprocess.run(
            command,
            input=f"{method} {password} {host} {port}\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Outline probe over IAP SSH exceeded the %d second timeout",
            PROBE_TIMEOUT_SECONDS,
        )
        return 1
    except OSError as error:
        logger.error("Could not start gcloud SSH command: %s", error)
        return 1

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        logger.error(
            "Outline probe failed with exit status %d; "
            "sensitive command output was suppressed",
            result.returncode,
        )
        return result.returncode or 1

    logger.info("EU Squid is reachable from %s through Outline", RU_INSTANCE_NAME)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ru-server actions.")
    parser.add_argument(
        "action",
        choices=("probe-outline-squid",),
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
