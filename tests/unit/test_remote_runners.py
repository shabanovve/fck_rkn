import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fck_rkn.gcp.runner import GcloudIapRunner
from fck_rkn.remote.runner import RemoteStartError, RemoteTimeoutError
from fck_rkn.remote.ssh import SshRunner

RUN = "fck_rkn.remote.subprocess_runner.subprocess.run"


class GcloudIapRunnerTests(unittest.TestCase):
    runner = GcloudIapRunner("ru-server", "fck-rkn-test", "europe-north1-a")

    def test_builds_iap_command_and_passes_stdin_out_of_argv(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with patch(RUN, return_value=completed) as run:
            result = self.runner.run("echo hi", "secret", 30)

        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["gcloud", "compute", "ssh", "ru-server"])
        self.assertIn("--project=fck-rkn-test", argv)
        self.assertIn("--zone=europe-north1-a", argv)
        self.assertIn("--tunnel-through-iap", argv)
        self.assertIn("--command=echo hi", argv)
        self.assertNotIn("secret", " ".join(argv))
        self.assertEqual(run.call_args.kwargs["input"], "secret")
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual((result.exit_code, result.stdout), (0, "ok"))

    def test_streams_output_when_requested(self) -> None:
        completed = subprocess.CompletedProcess([], 3, stdout=None, stderr=None)
        with patch(RUN, return_value=completed) as run:
            result = self.runner.run("cmd", None, 5, stream_output=True)

        self.assertNotIn("capture_output", run.call_args.kwargs)
        self.assertIn("stdout", run.call_args.kwargs)
        self.assertEqual((result.exit_code, result.stdout), (3, ""))

    def test_maps_timeout_and_start_failures(self) -> None:
        with patch(RUN, side_effect=subprocess.TimeoutExpired("gcloud", 1)):
            with self.assertRaises(RemoteTimeoutError):
                self.runner.run("cmd", None, 1)
        with patch(RUN, side_effect=FileNotFoundError("gcloud")):
            with self.assertRaises(RemoteStartError):
                self.runner.run("cmd", None, 1)


class SshRunnerTests(unittest.TestCase):
    runner = SshRunner("host.example", "deploy", Path("/keys/id"), port=2222)

    def test_builds_non_interactive_ssh_command(self) -> None:
        argv = self.runner.build_command("echo hi")

        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-2:], ["deploy@host.example", "echo hi"])
        self.assertEqual(argv[argv.index("-i") + 1], "/keys/id")
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertIn("BatchMode=yes", argv)
        self.assertNotIn("gcloud", argv)

    def test_run_passes_stdin_through_input(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch(RUN, return_value=completed) as run:
            self.runner.run("cmd", "secret", 9)

        self.assertEqual(run.call_args.kwargs["input"], "secret")
        self.assertNotIn("secret", " ".join(run.call_args.args[0]))

    def test_run_streams_a_file_stdin_as_binary(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "payload.bin"
            payload.write_bytes(b"\x1f\x8b\x00binary")
            seen = {}

            def fake_run(argv, **kwargs):
                seen["data"] = kwargs["stdin"].read()
                seen["kwargs"] = kwargs
                return completed

            with patch(RUN, side_effect=fake_run):
                self.runner.run("cmd", payload, 9)

        self.assertEqual(seen["data"], b"\x1f\x8b\x00binary")
        self.assertNotIn("input", seen["kwargs"])

    def test_missing_stdin_file_is_a_start_failure(self) -> None:
        with self.assertRaises(RemoteStartError):
            self.runner.run("cmd", Path("/nonexistent/payload.bin"), 9)


if __name__ == "__main__":
    unittest.main()
