"""Plain OpenSSH runner for hosts reachable directly (production)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fck_rkn.remote.runner import RemoteResult
from fck_rkn.remote.subprocess_runner import run_transport


@dataclass(frozen=True)
class SshRunner:
    host: str
    user: str
    key_path: Path
    port: int = 22

    def build_command(self, command: str) -> list[str]:
        return [
            "ssh",
            "-i",
            str(self.key_path),
            "-p",
            str(self.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            f"{self.user}@{self.host}",
            command,
        ]

    def run(
        self,
        command: str,
        stdin: str | Path | None,
        timeout: int | None,
        stream_output: bool = False,
    ) -> RemoteResult:
        return run_transport(self.build_command(command), stdin, timeout, stream_output)
