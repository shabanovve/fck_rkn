"""Create and fill ``config/test.env`` from the template using GCP access."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from typing import Protocol
from pathlib import Path

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError

from fck_rkn.config import CONFIG_FILE, TEST_PROJECT_ID
from fck_rkn.gcp.compute import GcpInspector

TEMPLATE_FILE = CONFIG_FILE.with_name("test.env.example")
AUTOMATIC_VARIABLES = ("GCP_SUBNET_CIDR",)

logger = logging.getLogger(__name__)



class GcpLookups(Protocol):
    def network_exists(self, project: str, network: str) -> bool: ...

    def list_subnets(
        self, project: str, zone: str, network: str
    ) -> dict[str, str]: ...

    def image_family_exists(self, project: str, family: str) -> bool: ...

    def machine_type_exists(
        self, project: str, zone: str, machine_type: str
    ) -> bool: ...


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _template_blocks(template: str) -> dict[str, str]:
    """Map each template variable to its comment and assignment lines."""
    blocks: dict[str, str] = {}
    lines: list[str] = []
    for line in template.splitlines():
        if line.startswith("#") or ("=" in line and line.strip()):
            lines.append(line)
            if not line.startswith("#"):
                blocks[line.split("=", 1)[0]] = "\n".join(lines)
                lines = []
        else:
            lines = []
    return blocks


def _set_value(text: str, name: str, value: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == name and "=" in line:
            lines[index] = f"{name}={value}"
            return "\n".join(lines) + "\n"
    return text.rstrip("\n") + f"\n{name}={value}\n"


def configure_test_env(
    config_path: Path,
    template_path: Path,
    gcp: GcpLookups,
) -> list[str]:
    """Fill the configuration; return one report line per change.

    The file is created from the template when missing. Values the operator
    already set are kept. The network, image family and machine type must
    exist in GCP. If the configured subnetwork does not exist and the network
    has exactly one subnetwork in the region, that one is selected; the CIDR
    is read from the subnetwork. Nothing is written when a check or a GCP
    read fails.
    """
    template = template_path.read_text(encoding="utf-8")
    report: list[str] = []
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    if original is not None:
        text = original
    else:
        text = template
        for name in AUTOMATIC_VARIABLES:
            text = _set_value(text, name, "")
        report.append(f"Created {config_path.name} from {template_path.name}")
    current = _parse(text)
    defaults = _parse(template)

    for name, block in _template_blocks(template).items():
        if name not in current:
            if name in AUTOMATIC_VARIABLES:
                block = block.rsplit("\n", 1)[0] + f"\n{name}="
                current[name] = ""
            else:
                current[name] = defaults[name]
            text = text.rstrip("\n") + f"\n\n{block}\n"
            report.append(f"Added missing variable {name}")

    project = current.get("GCP_PROJECT_ID", "")
    if project != TEST_PROJECT_ID:
        raise ValueError(f"GCP_PROJECT_ID must be {TEST_PROJECT_ID!r}")
    zone = current["GCP_ZONE"]
    network = current["GCP_NETWORK"]
    if not gcp.network_exists(project, network):
        raise ValueError(f"GCP_NETWORK {network!r} does not exist in {project}")
    subnets = gcp.list_subnets(project, zone, network)
    subnetwork = current["GCP_SUBNETWORK"]
    if subnetwork not in subnets:
        if len(subnets) != 1:
            raise ValueError(
                f"GCP_SUBNETWORK {subnetwork!r} does not exist in network "
                f"{network!r}; available: {', '.join(sorted(subnets)) or 'none'}"
            )
        subnetwork = next(iter(subnets))
        text = _set_value(text, "GCP_SUBNETWORK", subnetwork)
        report.append(f"Set GCP_SUBNETWORK={subnetwork}")
    if not gcp.image_family_exists(
        current["GCP_IMAGE_PROJECT"], current["GCP_IMAGE_FAMILY"]
    ):
        raise ValueError("the configured image family does not exist")
    if not gcp.machine_type_exists(project, zone, current["GCP_MACHINE_TYPE"]):
        raise ValueError(
            f"GCP_MACHINE_TYPE {current['GCP_MACHINE_TYPE']!r} is not "
            f"available in {zone}"
        )

    actual_cidr = subnets[subnetwork]
    if not current.get("GCP_SUBNET_CIDR"):
        text = _set_value(text, "GCP_SUBNET_CIDR", actual_cidr)
        report.append(f"Set GCP_SUBNET_CIDR={actual_cidr}")
    elif current["GCP_SUBNET_CIDR"] != actual_cidr:
        report.append(
            f"Warning: GCP_SUBNET_CIDR={current['GCP_SUBNET_CIDR']} differs "
            f"from the subnetwork range {actual_cidr}; left unchanged"
        )

    if text != original:
        _write_atomically(config_path, text)
    return report


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".test.env.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def configure_main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        report = configure_test_env(CONFIG_FILE, TEMPLATE_FILE, GcpInspector())
    except GoogleAuthError:
        print("GCP authentication failed; the configuration was not changed.", file=sys.stderr)
        return 3
    except GoogleAPICallError as error:
        print(f"GCP request failed ({type(error).__name__}); the configuration was not changed.", file=sys.stderr)
        return 14
    except (OSError, ValueError) as error:
        print(f"Configuration failed: {error}", file=sys.stderr)
        return 2
    for line in report:
        print(line)
    if not report:
        print(f"{CONFIG_FILE.name} is already complete; nothing changed.")
    return 0
