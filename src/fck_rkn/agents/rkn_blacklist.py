"""Fetch, validate and transactionally activate the shared crawler blacklist."""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


BLACKLIST_URL = (
    "https://raw.githubusercontent.com/C24Be/AS_Network_List/main/"
    "blacklists_nftables/blacklist.nft"
)
GITHUB_META_URL = "https://api.github.com/meta"
FETCH_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
USER_AGENT = "fck-rkn-blacklist-updater"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError("Could not fetch the required firewall update data") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Firewall update data exceeds the size limit")
    return content


def _network_values(value: object, source: str, version: int) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{source} must contain networks")
    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{source} contains a non-string network")
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as error:
            raise ValueError(f"{source} contains an invalid network") from error
        if network.version != version:
            raise ValueError(f"{source} contains a network of the wrong IP version")
        networks.add(network)
    return sorted(
        (network.with_prefixlen for network in ipaddress.collapse_addresses(networks)),
        key=lambda item: (
            int(ipaddress.ip_network(item).network_address),
            ipaddress.ip_network(item).prefixlen,
        ),
    )


def parse_network_set(content: str, set_name: str, version: int) -> list[str]:
    """Extract and validate one non-empty address set from nft source."""
    match = re.search(
        rf"\bset\s+{re.escape(set_name)}\s*\{{",
        content,
    )
    if match is None:
        raise ValueError(f"RKN blacklist is missing {set_name}")
    opening = match.end() - 1
    depth = 0
    closing = -1
    for index in range(opening, len(content)):
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                closing = index
                break
    if closing < 0:
        raise ValueError(f"RKN blacklist set {set_name} is malformed")
    body = content[match.end() : closing]
    elements = re.search(r"\belements\s*=\s*\{([^}]*)\}", body, re.DOTALL)
    if elements is None:
        raise ValueError(f"RKN blacklist set {set_name} has no elements")
    values = [
        item.strip()
        for item in elements.group(1).split(",")
        if item.strip()
    ]
    return _network_values(values, f"RKN {set_name}", version)


def parse_blacklist(content: str) -> tuple[list[str], list[str]]:
    """Extract and validate the IPv4 and IPv6 sets from the upstream nft file."""
    return (
        parse_network_set(content, "blacklist_v4", 4),
        parse_network_set(content, "blacklist_v6", 6),
    )


def parse_github_networks(content: bytes) -> list[str]:
    """Return published API and web address ranges used by the updater."""
    try:
        metadata = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise ValueError("GitHub network metadata is malformed") from error
    if not isinstance(metadata, dict):
        raise ValueError("GitHub network metadata is malformed")
    networks = []
    for category in ("api", "web"):
        values = metadata.get(category)
        if not isinstance(values, list) or not values:
            raise ValueError(f"GitHub metadata is missing {category} networks")
        category_versions: set[int] = set()
        for item in values:
            if not isinstance(item, str):
                raise ValueError(f"GitHub {category} contains a non-string network")
            try:
                network = ipaddress.ip_network(item, strict=True)
            except ValueError as error:
                raise ValueError(
                    f"GitHub {category} contains an invalid network"
                ) from error
            networks.append(network)
            category_versions.add(network.version)
        for version in (4, 6):
            if version not in category_versions:
                raise ValueError(
                    f"GitHub {category} metadata has no IPv{version} networks"
                )
    collapsed = [
        *ipaddress.collapse_addresses(
            network for network in set(networks) if network.version == 4
        ),
        *ipaddress.collapse_addresses(
            network for network in set(networks) if network.version == 6
        ),
    ]
    return sorted(
        (network.with_prefixlen for network in collapsed),
        key=lambda item: (
            ipaddress.ip_network(item).version,
            int(ipaddress.ip_network(item).network_address),
            ipaddress.ip_network(item).prefixlen,
        ),
    )


def fetch_blacklist() -> tuple[list[str], list[str]]:
    """Fetch and validate the current mixed IPv4/IPv6 crawler blacklist."""
    try:
        content = _fetch(BLACKLIST_URL).decode("utf-8")
    except UnicodeError as error:
        raise ValueError("RKN blacklist is not valid UTF-8") from error
    return parse_blacklist(content)


def fetch_github_networks() -> list[str]:
    """Fetch and validate published GitHub API and web networks."""
    return parse_github_networks(_fetch(GITHUB_META_URL))


def _reject_overlap(
    github_networks: Sequence[str],
    rkn_networks: Sequence[str],
) -> None:
    crawlers = [ipaddress.ip_network(value) for value in rkn_networks]
    for value in github_networks:
        network = ipaddress.ip_network(value)
        if any(
            network.version == crawler.version and network.overlaps(crawler)
            for crawler in crawlers
        ):
            raise ValueError("GitHub networks overlap an RKN crawler range")


def _elements(networks: Sequence[str]) -> str:
    return ", ".join(networks)


def _replace_set(
    rules: str, set_name: str, networks: Sequence[str], version: int
) -> str:
    set_pattern = re.compile(
        rf"(?ms)^  set {re.escape(set_name)} \{{\n(?P<body>.*?)^  \}}"
    )
    set_match = set_pattern.search(rules)
    if set_match is None:
        raise ValueError(f"Saved firewall rules are missing {set_name}")
    body = set_match.group("body")
    expected_type = "ipv4_addr" if version == 4 else "ipv6_addr"
    if not re.search(rf"(?m)^\s*type {expected_type}\s*$", body):
        raise ValueError(f"Saved firewall set {set_name} has an invalid type")
    element_pattern = re.compile(r"(?m)^(\s*elements\s*=\s*)\{[^}]*\}")
    if element_pattern.search(body) is None:
        raise ValueError(f"Saved firewall set {set_name} has no elements field")
    replacement_body = element_pattern.sub(
        lambda match: f"{match.group(1)}{{ {_elements(networks)} }}",
        body,
        count=1,
    )
    return (
        rules[: set_match.start("body")]
        + replacement_body
        + rules[set_match.end("body") :]
    )


def _replace_sets(
    rules: str,
    rkn_ipv4: Sequence[str],
    rkn_ipv6: Sequence[str],
    github_networks: Sequence[str] | None,
) -> str:
    updated = _replace_set(rules, "rkn_ipv4", rkn_ipv4, 4)
    updated = _replace_set(updated, "rkn_ipv6", rkn_ipv6, 6)
    if github_networks is not None:
        github_ipv4 = [value for value in github_networks if ":" not in value]
        github_ipv6 = [value for value in github_networks if ":" in value]
        updated = _replace_set(updated, "github_ipv4", github_ipv4, 4)
        updated = _replace_set(updated, "github_ipv6", github_ipv6, 6)
    return updated


def _contains_set(rules: str, set_name: str) -> bool:
    return re.search(rf"(?m)^  set {re.escape(set_name)} \{{", rules) is not None


def _run_nft(command: list[str], operation: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if not detail:
            detail = f"exit status {result.returncode}"
        raise RuntimeError(f"nftables {operation} failed: {detail}")


def update_firewall(rules_path: str, lock_path: str, host: str) -> None:
    """Fetch all needed data and atomically update saved and active nft rules."""
    if host not in ("ru", "eu"):
        raise ValueError("host must be ru or eu")
    rules_file = Path(rules_path)
    lock_file = Path(lock_path)
    with lock_file.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            original = rules_file.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError("Could not read the saved firewall rules") from error
        uses_github_sets = _contains_set(original, "github_ipv4") and _contains_set(
            original, "github_ipv6"
        )
        if _contains_set(original, "github_ipv4") != _contains_set(
            original, "github_ipv6"
        ):
            raise ValueError("Saved firewall rules contain incomplete GitHub sets")
        github_networks = fetch_github_networks() if uses_github_sets else None
        rkn_ipv4, rkn_ipv6 = fetch_blacklist()
        if github_networks is not None:
            _reject_overlap(github_networks, [*rkn_ipv4, *rkn_ipv6])
        candidate = _replace_sets(
            original, rkn_ipv4, rkn_ipv6, github_networks
        )
        candidate_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=rules_file.parent,
                prefix=f".{rules_file.name}.",
                delete=False,
            ) as staged:
                candidate_path = staged.name
                staged.write(candidate)
                staged.flush()
                os.fsync(staged.fileno())
            os.chmod(candidate_path, 0o600)
            _run_nft(
                ["nft", "--check", "--file", candidate_path],
                "candidate validation",
            )
            _run_nft(["nft", "--file", candidate_path], "candidate activation")
            try:
                os.replace(candidate_path, rules_file)
                candidate_path = None
            except OSError as error:
                try:
                    _run_nft(["nft", "--file", str(rules_file)], "rollback")
                except RuntimeError as rollback_error:
                    raise RuntimeError(
                        "Could not save the new firewall rules and active-rule "
                        f"rollback failed: {rollback_error}"
                    ) from error
                raise RuntimeError(
                    "Could not save the new firewall rules; previous active "
                    "rules were restored"
                ) from error
        finally:
            if candidate_path is not None:
                try:
                    os.unlink(candidate_path)
                except FileNotFoundError:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, choices=("ru", "eu"))
    parser.add_argument("--rules-file", required=True)
    parser.add_argument("--lock-file", required=True)
    args = parser.parse_args(argv)
    try:
        update_firewall(args.rules_file, args.lock_file, args.host)
    except (
        OSError,
        RuntimeError,
        ValueError,
        urllib.error.URLError,
    ) as error:
        print(f"RKN blacklist update failed: {error}", file=sys.stderr)
        return 1
    print("RKN blacklist firewall update succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
