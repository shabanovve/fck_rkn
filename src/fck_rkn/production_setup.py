"""Interactive creation of config/prod.env with read-only SSH discovery."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from dotenv import dotenv_values
from fck_rkn.production_config import (
    PROD_CONFIG_FILE,
    PROD_TEMPLATE_FILE,
    EuProductionSettings,
    ProductionConfigError,
    load_eu_production_settings,
)
from fck_rkn.remote.runner import (
    RemoteRunner,
    RemoteStartError,
    RemoteTimeoutError,
)


DISCOVERY_TIMEOUT_SECONDS = 20
METADATA_CANDIDATE = "169.254.169.254"
IGNORED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel")

LOCAL_NETWORKS_COMMAND = "ip -o addr show scope global 2>/dev/null"
DNS_COMMAND = (
    "resolvectl dns 2>/dev/null; "
    "grep -h '^nameserver' /etc/resolv.conf 2>/dev/null; true"
)
DHCP_COMMAND = (
    "cat /run/systemd/netif/leases/* 2>/dev/null; "
    "cat /var/lib/dhcp/dhclient*.leases /var/lib/NetworkManager/*.lease "
    "2>/dev/null; "
    "nmcli -t -f DHCP4.OPTION,DHCP6.OPTION device show 2>/dev/null; "
    "(journalctl -b --no-pager 2>/dev/null || sudo -n journalctl -b --no-pager "
    "2>/dev/null) | grep -i 'dhcp.*acquired from' | tail -n 5; true"
)
NTP_COMMAND = (
    "timedatectl show-timesync --property=CurrentNTPServer "
    "--property=SystemNTPServers --property=LinkNTPServers 2>/dev/null; "
    "chronyc -n sources 2>/dev/null; true"
)
METADATA_COMMAND = (
    f"timeout 3 bash -c '</dev/tcp/{METADATA_CANDIDATE}/80' >/dev/null 2>&1"
)

IPV4_RE = r"\d{1,3}(?:\.\d{1,3}){3}"
ADDRESS_TOKEN_RE = re.compile(r"[0-9A-Fa-f:.]+")

Prompt = Callable[[str], str]
Output = Callable[[str], None]


class ProductionSetupError(Exception):
    """Interactive configuration could not complete; nothing was written."""


def _valid_ips(tokens: list[str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        try:
            address = ipaddress.ip_address(token.strip("[]"))
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        if address.compressed not in result:
            result.append(address.compressed)
    return result


def parse_local_networks(output: str) -> list[str]:
    networks: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[2] not in ("inet", "inet6"):
            continue
        if fields[1].startswith(IGNORED_INTERFACE_PREFIXES):
            continue
        try:
            interface = ipaddress.ip_interface(fields[3])
        except ValueError:
            continue
        if interface.is_link_local:
            continue
        network = interface.network.with_prefixlen
        if network not in networks:
            networks.append(network)
    return networks


def parse_dns_addresses(output: str) -> list[str]:
    tokens: list[str] = []
    for line in output.splitlines():
        if line.startswith("nameserver"):
            tokens.extend(line.split()[1:2])
        elif ":" in line and not line.startswith("nameserver"):
            tokens.extend(
                token.split("#")[0].split("%")[0]
                for token in line.split(":", 1)[1].split()
                if ADDRESS_TOKEN_RE.fullmatch(token.split("#")[0].split("%")[0])
            )
    return _valid_ips(tokens)


def parse_dhcp_server(output: str) -> list[str]:
    tokens = re.findall(r"SERVER_ADDRESS=(" + IPV4_RE + ")", output)
    tokens += re.findall(r"acquired from (" + IPV4_RE + ")", output)
    tokens += re.findall(
        r"dhcp[-_]server[-_]identifier\D{1,4}(" + IPV4_RE + ")", output, re.I
    )
    return _valid_ips(tokens)


def parse_ntp_addresses(output: str) -> list[str]:
    tokens: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[0][:1] in "^#" and len(fields[0]) <= 2:
            tokens.append(fields[1])
        elif "=" in line:
            tokens.extend(line.split("=", 1)[1].split())
    return _valid_ips(tokens)


def _query(runner: RemoteRunner, command: str) -> tuple[int, str]:
    try:
        result = runner.run(command, None, DISCOVERY_TIMEOUT_SECONDS)
    except (RemoteTimeoutError, RemoteStartError):
        return 1, ""
    return result.exit_code, result.stdout


def discover_eu_settings(runner: RemoteRunner) -> dict[str, object]:
    """Read-only discovery. Settings that could not be found are omitted."""
    found: dict[str, object] = {}
    commands = (
        ("EU_LOCAL_NETWORKS", LOCAL_NETWORKS_COMMAND, parse_local_networks),
        ("EU_DNS_ADDRESSES", DNS_COMMAND, parse_dns_addresses),
        ("EU_DHCP_SERVER_ADDRESS", DHCP_COMMAND, parse_dhcp_server),
        ("EU_NTP_ADDRESSES", NTP_COMMAND, parse_ntp_addresses),
    )
    for name, command, parser in commands:
        _, output = _query(runner, command)
        values = parser(output)
        if not values:
            continue
        found[name] = values[0] if name == "EU_DHCP_SERVER_ADDRESS" else values
    return found


def discover_ru_settings(runner: RemoteRunner) -> dict[str, object]:
    """Read-only discovery of RU host service settings; omit unknown values."""
    found: dict[str, object] = {}
    commands = (
        ("RU_LOCAL_NETWORKS", LOCAL_NETWORKS_COMMAND, parse_local_networks),
        ("RU_DNS_ADDRESSES", DNS_COMMAND, parse_dns_addresses),
        ("RU_DHCP_SERVER_ADDRESS", DHCP_COMMAND, parse_dhcp_server),
        ("RU_NTP_ADDRESSES", NTP_COMMAND, parse_ntp_addresses),
    )
    for name, command, parser in commands:
        _, output = _query(runner, command)
        values = parser(output)
        if values:
            found[name] = (
                values[0] if name == "RU_DHCP_SERVER_ADDRESS" else values
            )
    return found


def _format(value: object) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value)


def _parse(name: str, raw: str) -> object:
    """Validate one operator-entered value with the loader's field rules."""
    raw = raw.strip()
    if name in ("EU_SSH_PORT", "RU_SSH_PORT"):
        if not raw.isdecimal() or not 1 <= int(raw) <= 65535:
            raise ValueError("must be a TCP port from 1 through 65535")
        return int(raw)
    if name in (
        "EU_DNS_ADDRESSES",
        "EU_NTP_ADDRESSES",
        "EU_LOCAL_NETWORKS",
        "RU_DNS_ADDRESSES",
        "RU_NTP_ADDRESSES",
        "RU_LOCAL_NETWORKS",
        "RU_SERVER_TO_GITHUB_ADDRESSES",
    ):
        if raw.startswith("[") and raw.endswith("]"):
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError("invalid JSON array")
            if not isinstance(items, list):
                raise ValueError("must be a JSON array")
            items = [str(item) for item in items]
        else:
            items = [item for item in re.split(r"[,\s]+", raw) if item]
        if name in ("EU_LOCAL_NETWORKS", "RU_LOCAL_NETWORKS", "RU_SERVER_TO_GITHUB_ADDRESSES"):
            return EuProductionSettings.validate_local_networks(items)
        return EuProductionSettings.validate_address_lists(items)
    if name in ("EU_SSH_USER", "RU_SSH_USER"):
        return EuProductionSettings.validate_ssh_user(raw)
    if name in ("EU_SSH_KEY_PATH", "RU_SSH_KEY_PATH"):
        return str(EuProductionSettings.validate_ssh_key_path(Path(raw)))
    return EuProductionSettings.validate_ip_address(raw)


HINTS = {
    "EU_SERVER_ADDRESS": "IPv4 or IPv6 address of eu-server",
    "EU_SSH_USER": "SSH account on eu-server",
    "EU_SSH_KEY_PATH": "absolute path to the SSH private key on this machine",
    "EU_SSH_PORT": "SSH TCP port",
    "RU_SERVER_ADDRESS": "IPv4 or IPv6 address of ru-server (cannot be detected)",
    "RU_SSH_USER": "SSH account on ru-server",
    "RU_SSH_KEY_PATH": "absolute path to the RU SSH private key on this machine",
    "RU_SSH_PORT": "RU SSH TCP port",
    "EU_DNS_ADDRESSES": "IPv4/IPv6 resolver addresses, comma-separated",
    "EU_DHCP_SERVER_ADDRESS": "IPv4 address of the DHCP server",
    "EU_NTP_ADDRESSES": "IPv4/IPv6 NTP server addresses, comma-separated",
    "EU_LOCAL_NETWORKS": "CIDR networks local to eu-server, comma-separated",
    "RU_DNS_ADDRESSES": "RU IPv4/IPv6 resolver addresses, comma-separated",
    "RU_DHCP_SERVER_ADDRESS": "IPv4 or IPv6 address of the RU DHCP server",
    "RU_NTP_ADDRESSES": "RU IPv4/IPv6 NTP server addresses, comma-separated",
    "RU_LOCAL_NETWORKS": "CIDR networks local to ru-server, comma-separated",
    "RU_SERVER_TO_GITHUB_ADDRESSES": "GitHub egress CIDRs, comma-separated",
}


def _ask(
    name: str,
    prompt: Prompt,
    output: Output,
    detected: object | None = None,
    default: str | None = None,
) -> object:
    suggestion = default if detected is None else _format(detected)
    label = f"{name} ({HINTS[name]})"
    if detected is not None:
        label += f" [detected: {suggestion}; Enter to accept]"
    elif default is not None:
        label += f" [{default}]"
    while True:
        raw = prompt(f"{label}: ").strip()
        if not raw:
            if detected is not None:
                return detected
            if default is None:
                output(f"{name} is required.")
                continue
            raw = default
        try:
            return _parse(name, raw)
        except ValueError as error:
            output(f"{name} {error}")


def render_config(template: str, values: dict[str, object]) -> str:
    for name, value in values.items():
        text = json.dumps(value) if isinstance(value, list) else str(value)
        template, count = re.subn(
            rf"^{name}=.*$", lambda _: f"{name}={text}", template, flags=re.M
        )
        if count != 1:
            raise ProductionSetupError(f"Template has no unique {name} entry")
    return template


def _publish(content: str, path: Path, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            load_eu_production_settings(temporary)
        except ProductionConfigError as error:
            raise ProductionSetupError(str(error)) from error
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ProductionSetupError(
                    f"{path.name} was created concurrently; nothing was written"
                ) from error
    finally:
        temporary.unlink(missing_ok=True)


def configure_interactively(
    runner_factory: Callable[[str, str, Path, int], RemoteRunner],
    prompt: Prompt = input,
    output: Output = print,
    config_path: Path = PROD_CONFIG_FILE,
    template_path: Path = PROD_TEMPLATE_FILE,
) -> None:
    """Ask for SSH details, discover the rest over SSH, confirm, and write.

    Nothing is written when SSH fails, validation fails or the operator
    declines. An existing file is replaced only after explicit confirmation.
    """
    replace = config_path.exists()
    if replace:
        answer = prompt(f"{config_path.name} exists. Overwrite it? [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            raise ProductionSetupError("Existing configuration was kept")

    template_defaults = {}
    if template_path.exists():
        template_defaults = {k: v for k, v in dotenv_values(template_path).items() if v and not v.startswith("SET_TO_")}

    values: dict[str, object] = {}
    for name, fallback in (
        ("EU_SERVER_ADDRESS", None),
        ("EU_SSH_USER", None),
        ("EU_SSH_KEY_PATH", None),
        ("EU_SSH_PORT", "22"),
    ):
        values[name] = _ask(
            name, prompt, output, default=template_defaults.get(name, fallback)
        )

    runner = runner_factory(
        str(values["EU_SERVER_ADDRESS"]),
        str(values["EU_SSH_USER"]),
        Path(str(values["EU_SSH_KEY_PATH"])),
        int(values["EU_SSH_PORT"]),  # type: ignore[call-overload]
    )
    exit_code, _ = _query(runner, "true")
    if exit_code != 0:
        raise ProductionSetupError(
            "Could not connect to eu-server over SSH. Check the address, "
            "account, key and port, and make sure the host key is already "
            "trusted (connect once with ssh manually). Nothing was written."
        )

    output("Connected. Detecting settings on eu-server (read-only)...")
    detected = discover_eu_settings(runner)
    for name in (
        "EU_DNS_ADDRESSES",
        "EU_DHCP_SERVER_ADDRESS",
        "EU_NTP_ADDRESSES",
        "EU_LOCAL_NETWORKS",
    ):
        values[name] = _ask(
            name, prompt, output, detected=detected.get(name), default=template_defaults.get(name)
        )

    for name, fallback in (
        ("RU_SERVER_ADDRESS", None),
        ("RU_SSH_USER", str(values.get("EU_SSH_USER", ""))), 
        ("RU_SSH_KEY_PATH", str(values.get("EU_SSH_KEY_PATH", ""))), 
        ("RU_SSH_PORT", str(values.get("EU_SSH_PORT", "22"))),
    ):
        values[name] = _ask(
            name, prompt, output, default=template_defaults.get(name, fallback)
        )

    ru_runner = runner_factory(
        str(values["RU_SERVER_ADDRESS"]),
        str(values["RU_SSH_USER"]),
        Path(str(values["RU_SSH_KEY_PATH"])),
        int(values["RU_SSH_PORT"]),  # type: ignore[call-overload]
    )
    exit_code, _ = _query(ru_runner, "true")
    if exit_code != 0:
        raise ProductionSetupError(
            "Could not connect to ru-server over SSH. Check the address, "
            "account, key and port, and make sure the host key is already "
            "trusted (connect once with ssh manually). Nothing was written."
        )

    output("Connected. Detecting settings on ru-server (read-only)...")
    detected_ru = discover_ru_settings(ru_runner)
    for name in (
        "RU_DNS_ADDRESSES",
        "RU_DHCP_SERVER_ADDRESS",
        "RU_NTP_ADDRESSES",
        "RU_LOCAL_NETWORKS",
    ):
        values[name] = _ask(
            name, prompt, output, detected=detected_ru.get(name), default=template_defaults.get(name)
        )
    values["RU_SERVER_TO_GITHUB_ADDRESSES"] = _ask(
        "RU_SERVER_TO_GITHUB_ADDRESSES", prompt, output, default=template_defaults.get("RU_SERVER_TO_GITHUB_ADDRESSES")
    )

    output("\nConfiguration to write:")
    for name, value in values.items():
        output(f"  {name}={_format(value)}")
    if prompt(f"Write {config_path.name}? [y/N]: ").strip().lower() not in ("y", "yes"):
        raise ProductionSetupError("Declined; nothing was written")

    content = render_config(template_path.read_text(encoding="utf-8"), values)
    _publish(content, config_path, replace)
    output(f"Wrote {config_path.name}.")
