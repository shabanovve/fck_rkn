"""Shared subprocess execution for command-line based remote runners."""

from __future__ import annotations

import subprocess
import sys

from fck_rkn.remote.runner import RemoteResult, RemoteStartError, RemoteTimeoutError


def run_transport(
    argv: list[str],
    stdin: str | None,
    timeout: int | None,
    stream_output: bool,
) -> RemoteResult:
    """Run a transport command, mapping failures to runner exceptions."""
    options: dict = {"input": stdin, "check": False, "text": True, "timeout": timeout}
    if stream_output:
        options.update(stdout=sys.stdout, stderr=sys.stderr)
    else:
        options["capture_output"] = True
    try:
        result = subprocess.run(argv, **options)
    except subprocess.TimeoutExpired as error:
        raise RemoteTimeoutError() from error
    except OSError as error:
        raise RemoteStartError(str(error)) from error
    return RemoteResult(result.returncode, result.stdout or "", result.stderr or "")
