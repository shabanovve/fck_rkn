"""gcloud IAP SSH runner. Test environment only; agents must not import it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fck_rkn.remote.runner import RemoteResult
from fck_rkn.remote.subprocess_runner import run_transport


@dataclass(frozen=True)
class GcloudIapRunner:
    instance_name: str
    project: str
    zone: str

    def build_command(self, command: str) -> list[str]:
        return [
            "gcloud",
            "compute",
            "ssh",
            self.instance_name,
            f"--project={self.project}",
            f"--zone={self.zone}",
            "--tunnel-through-iap",
            f"--command={command}",
            "--quiet",
        ]

    def run(
        self,
        command: str,
        stdin: str | Path | None,
        timeout: int | None,
        stream_output: bool = False,
    ) -> RemoteResult:
        return run_transport(self.build_command(command), stdin, timeout, stream_output)
