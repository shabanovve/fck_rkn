"""Remote shell, Python and Docker scripts executed on test VMs.

The scripts live as real files in ``scripts/`` so they can be linted and read
in isolation. ``render`` loads one and fills ``@NAME@`` placeholders.
"""

from __future__ import annotations

import re
from importlib.resources import files

_INCLUDE = re.compile(r"^#include (\S+)$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"@([A-Z][A-Z0-9_]*)@")


def _read(name: str) -> str:
    return (files(__package__) / "scripts" / name).read_text(encoding="utf-8")


def render(name: str, **params: str | int) -> str:
    """Return script ``name`` with includes expanded and placeholders filled.

    Parameters are given in lowercase (``container_name`` fills
    ``@CONTAINER_NAME@``). Unknown or unused placeholders raise ``ValueError``
    so a typo can never reach a remote host.
    """
    text = _read(name)
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    text = _INCLUDE.sub(lambda match: _read(match.group(1)).rstrip("\n"), text)

    values = {key.upper(): str(value) for key, value in params.items()}
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Script {name} has no value for @{key}@")
        used.add(key)
        return values[key]

    text = _PLACEHOLDER.sub(substitute, text)
    unused = set(values) - used
    if unused:
        raise ValueError(f"Script {name} does not use: {', '.join(sorted(unused))}")
    return text.rstrip("\n")
