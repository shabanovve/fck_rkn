from __future__ import annotations

from fck_rkn.remote.runner import RemoteResult


class FakeRunner:
    """Records calls instead of reaching a host."""

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "", error=None
    ) -> None:
        self.result = RemoteResult(exit_code, stdout, stderr)
        self.error = error
        self.calls: list[tuple[str, str | None, int, bool]] = []

    def run(self, command, stdin, timeout, stream_output=False):
        self.calls.append((command, stdin, timeout, stream_output))
        if self.error:
            raise self.error
        return self.result

    @property
    def command(self) -> str:
        return self.calls[-1][0]

    @property
    def stdin(self) -> str | None:
        return self.calls[-1][1]
