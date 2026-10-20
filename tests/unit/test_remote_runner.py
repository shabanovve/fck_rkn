import unittest
from contextlib import redirect_stdout
from io import StringIO

from fck_rkn.remote.runner import (
    RemoteResult,
    RemoteStartError,
    RemoteTimeoutError,
    execute,
)


class FakeRunner:
    def __init__(self, result=None, error=None) -> None:
        self.result = result or RemoteResult(0)
        self.error = error
        self.calls = []

    def run(self, command, stdin, timeout, stream_output=False):
        self.calls.append((command, stdin, timeout, stream_output))
        if self.error:
            raise self.error
        return self.result


class ExecuteTests(unittest.TestCase):
    def test_passes_arguments_to_runner_and_returns_zero(self) -> None:
        runner = FakeRunner()

        code = execute(runner, "cmd", "Step", 5, stdin="secret", stream_output=True)

        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [("cmd", "secret", 5, True)])

    def test_returns_remote_exit_code_without_logging_output(self) -> None:
        runner = FakeRunner(RemoteResult(7, stdout="out-secret", stderr="err-secret"))

        with self.assertLogs("fck_rkn.remote.runner", level="ERROR") as logs:
            code = execute(runner, "cmd", "Step", 5)

        self.assertEqual(code, 7)
        self.assertIn("exit status 7", logs.output[0])
        self.assertNotIn("secret", logs.output[0])

    def test_timeout_and_start_errors_return_one(self) -> None:
        for error in (RemoteTimeoutError(), RemoteStartError("no binary")):
            with self.subTest(error=type(error).__name__):
                with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
                    self.assertEqual(
                        execute(FakeRunner(error=error), "cmd", "Step", 5), 1
                    )

    def test_prints_stdout_only_when_requested(self) -> None:
        runner = FakeRunner(RemoteResult(0, stdout="hello"))
        quiet, loud = StringIO(), StringIO()
        with redirect_stdout(quiet):
            execute(runner, "cmd", "Step", 5)
        with redirect_stdout(loud):
            execute(runner, "cmd", "Step", 5, print_output=True)

        self.assertEqual(quiet.getvalue(), "")
        self.assertEqual(loud.getvalue(), "hello\n")


if __name__ == "__main__":
    unittest.main()
