"""Platform-independent interface for running commands on a remote host."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


logger = logging.getLogger(__name__)


class RemoteTimeoutError(Exception):
    """The remote command exceeded its time limit."""


class RemoteStartError(Exception):
    """The transport could not be started (for example, a missing binary)."""


@dataclass(frozen=True)
class RemoteResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class RemoteRunner(Protocol):
    """Runs a shell command on one remote host.

    Implementations must pass stdin to the command without exposing it in
    argv, raise RemoteTimeoutError on timeout and RemoteStartError when the
    transport cannot start. With stream_output the output goes straight to the
    caller's stdout/stderr and the result carries no captured text. A Path
    stdin is streamed from that file, for binary payloads.
    """

    def run(
        self,
        command: str,
        stdin: str | Path | None,
        timeout: int | None,
        stream_output: bool = False,
    ) -> RemoteResult: ...


def execute(
    runner: RemoteRunner,
    command: str,
    description: str,
    timeout: int | None,
    stdin: str | Path | None = None,
    stream_output: bool = False,
    print_output: bool = False,
    print_stderr: bool = False,
) -> int:
    """Run command and return an exit code, logging failures without output.

    Remote output is never logged because it may contain secrets; with
    print_output the captured stdout is printed for the caller, and with
    print_stderr the captured stderr is printed when the command fails.
    A timeout of None waits indefinitely.
    """
    try:
        result = runner.run(command, stdin, timeout, stream_output)
    except RemoteTimeoutError:
        logger.error("%s exceeded the %d second timeout", description, timeout)
        return 1
    except RemoteStartError as error:
        logger.error("Could not start the remote command runner: %s", error)
        return 1

    if print_output and result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.exit_code != 0:
        logger.error(
            "%s failed with exit status %d; sensitive command output was suppressed",
            description,
            result.exit_code,
        )
        if print_stderr and result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.exit_code
    return 0
