"""Shared subprocess execution for command-line based remote runners."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fck_rkn.remote.runner import RemoteResult, RemoteStartError, RemoteTimeoutError


def run_transport(
    argv: list[str],
    stdin: str | Path | None,
    timeout: int | None,
    stream_output: bool,
) -> RemoteResult:
    """Run a transport command, mapping failures to runner exceptions.

    A Path stdin is streamed from that file as raw bytes, so a large binary
    payload is neither loaded into memory nor exposed in argv.
    """
    if isinstance(stdin, Path):
        try:
            with stdin.open("rb") as handle:
                return _run(argv, {"stdin": handle}, timeout, stream_output)
        except OSError as error:
            raise RemoteStartError(str(error)) from error
    return _run(argv, {"input": stdin}, timeout, stream_output)


def _run(
    argv: list[str], options: dict, timeout: int | None, stream_output: bool
) -> RemoteResult:
    options.update(check=False, text=True, timeout=timeout)
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
