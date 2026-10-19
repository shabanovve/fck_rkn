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
OUTLINE_CONTAINER_NAME = "fck-rkn-outline-shadowbox"
OUTLINE_BASE_IMAGE = "quay.io/outline/shadowbox:stable"
OUTLINE_IMAGE = "fck-rkn-shadowbox:no-public-ip-check"
OUTLINE_SS_SERVER_VERSION = "v1.7.3"
# Test environment only: the patched outline-ss-server accepts private target
# addresses so the RU route can reach eu-server's private Squid endpoint.
OUTLINE_IMAGE_DOCKERFILE = "\n".join(
    (
        "FROM golang:1.24 AS build",
        "RUN git clone -q --depth 1 --branch "
        f"{OUTLINE_SS_SERVER_VERSION} "
        "https://github.com/Jigsaw-Code/outline-ss-server /src",
        "WORKDIR /src",
        "RUN sed -i 's/^\\(func RequirePublicIP(ip net.IP) error {\\)$/\\1\\n\\treturn nil/' "
        "net/private_net.go \\",
        " && grep -A1 'func RequirePublicIP' net/private_net.go | grep -q 'return nil' \\",
        " && CGO_ENABLED=0 go build -o /outline-ss-server ./cmd/outline-ss-server",
        f"FROM {OUTLINE_BASE_IMAGE}",
        "COPY --from=build /outline-ss-server /opt/outline-server/bin/outline-ss-server",
    )
)
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
    remote_command = " && ".join(
        (
            "command -v docker >/dev/null 2>&1 || "
            "(sudo apt-get update && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io)",
            f"if [ \"$(sudo docker inspect --format='{{{{.State.Running}}}}' "
            f"{SQUID_CONTAINER_NAME} 2>/dev/null || true)\" = true ]; then "
            'echo "Squid container is already running; leaving it unchanged"; '
            "exit 0; fi",
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


def deploy_outline(settings: TestEnvironmentSettings) -> int:
    """Install the official stable Outline Server using eu-server's private IP."""
    remote_command = "\n".join(
        (
            "set -euo pipefail",
            f"container_name={OUTLINE_CONTAINER_NAME}",
            f"expected_image={OUTLINE_IMAGE}",
            'if sudo docker container inspect "$container_name" >/dev/null 2>&1; then',
            '  actual_image="$(sudo docker inspect --format="{{.Config.Image}}" "$container_name")"',
            '  actual_state="$(sudo docker inspect --format="{{.State.Status}}" "$container_name")"',
            '  if [ "$actual_image" = "$expected_image" ] && [ "$actual_state" = running ]; then',
            '    echo "Outline Server container is already running; leaving it unchanged"',
            "    exit 0",
            "  fi",
            f'  if [ "$actual_image" = {OUTLINE_BASE_IMAGE} ]; then',
            '    echo "Replacing the stock Outline Server container with the patched image"',
            '    sudo docker rm -f "$container_name" watchtower >/dev/null 2>&1 || true',
            "  else",
            '    echo "Existing Outline Server container differs; refusing to replace it" >&2',
            "    exit 1",
            "  fi",
            "fi",
            'if sudo docker container inspect watchtower >/dev/null 2>&1; then',
            '  echo "A watchtower container already exists; refusing to replace it" >&2',
            "  exit 1",
            "fi",
            'if ! command -v docker >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then',
            "  sudo apt-get update",
            "  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io curl",
            "fi",
            "sudo systemctl enable --now docker",
            'echo "Building the Outline Server image without the public-IP target check"',
            "sudo docker build -t "
            f"{OUTLINE_IMAGE} -f - \"$(mktemp -d)\" <<'FCK_RKN_DOCKERFILE'",
            OUTLINE_IMAGE_DOCKERFILE,
            "FCK_RKN_DOCKERFILE",
            'private_ip="$(curl --fail --silent --show-error '
            "-H 'Metadata-Flavor: Google' "
            "'http://metadata.google.internal/computeMetadata/v1/"
            "instance/network-interfaces/0/ip')\"",
            'echo "Installing official Outline Server stable image on the private VM address"',
            f"curl --fail --silent --show-error {OUTLINE_INSTALLER_URL} | "
            "sudo env "
            f"CONTAINER_NAME={OUTLINE_CONTAINER_NAME} "
            f"SB_IMAGE={OUTLINE_IMAGE} "
            f"SHADOWBOX_DIR={OUTLINE_STATE_DIRECTORY} "
            "bash -s -- "
            '--hostname "$private_ip" '
            f"--api-port {OUTLINE_API_PORT} "
            f"--keys-port {OUTLINE_ACCESS_KEY_PORT} "
            ">/dev/null 2>&1 || { "
            "echo 'Official Outline Server installer failed; sensitive installer output was suppressed' >&2; "
            "exit 1; }",
            'echo "Outline Server deployment completed on the private VM address"',
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

    remote_command = "\n".join(
        (
            "set -euo pipefail",
            "sudo python3 - <<'PY'",
            "import json",
            "import ssl",
            "import urllib.request",
            f"user_name = {user_name!r}",
            f"state_directory = {OUTLINE_STATE_DIRECTORY!r}",
            "with open(f'{state_directory}/access.txt', encoding='utf-8') as state:",
            "    api_url = next((",
            "        line.removeprefix('apiUrl:').strip()",
            "        for line in state",
            "        if line.startswith('apiUrl:')",
            "    ), None)",
            "if not api_url:",
            "    raise RuntimeError('Outline management endpoint is unavailable')",
            "context = ssl._create_unverified_context()",
            "keys_endpoint = f'{api_url}/access-keys'",
            "with urllib.request.urlopen(keys_endpoint, context=context, timeout=30) as response:",
            "    keys = json.load(response).get('accessKeys')",
            "if not isinstance(keys, list):",
            "    raise RuntimeError('Outline returned an invalid access-key list')",
            "matching_keys = [key for key in keys if key.get('name') == user_name]",
            "if len(matching_keys) > 1:",
            "    raise RuntimeError('More than one Outline key has this user name')",
            "if matching_keys:",
            "    key = matching_keys[0]",
            "else:",
            "    request = urllib.request.Request(",
            "        keys_endpoint,",
            "        data=json.dumps({'name': user_name}).encode(),",
            "        headers={'Content-Type': 'application/json'},",
            "        method='POST',",
            "    )",
            "    with urllib.request.urlopen(request, context=context, timeout=30) as response:",
            "        key = json.load(response)",
            "if key.get('name') != user_name:",
            "    raise RuntimeError('Outline returned a key for a different user')",
            "access_url = key.get('accessUrl')",
            "if not isinstance(access_url, str) or not access_url.startswith('ss://'):",
            "    raise RuntimeError('Outline did not return a valid access key')",
            "print(access_url)",
            "PY",
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
        return deploy_outline(settings)
    if args.action == "create-outline-user":
        access_url = create_outline_user(settings, args.name)
        if access_url is None:
            return 1
        print(access_url)
        return 0
    return run_youtube_ping(settings)


if __name__ == "__main__":
    raise SystemExit(main())
