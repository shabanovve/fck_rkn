from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fake_runner import FakeRunner

from fck_rkn.agents.outline import OUTLINE_IMAGE, load_outline_image
from fck_rkn.remote.runner import RemoteTimeoutError


class LoadOutlineImageTests(unittest.TestCase):
    def test_sends_the_archive_through_stdin_not_the_command(self) -> None:
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "outline-image.tar.gz"
            archive.write_bytes(b"archive data")

            self.assertEqual(load_outline_image(runner, archive), 0)

        self.assertEqual(runner.stdin, archive)
        self.assertIn(f"expected_image={OUTLINE_IMAGE}", runner.command)
        self.assertIn(" 12 | gunzip | sudo docker load", runner.command)
        self.assertIn("Loading the prebuilt Outline Server image: 0%", runner.command)
        self.assertIn("reported + 5", runner.command)
        self.assertIn("min(99,", runner.command)
        self.assertIn("Loading the prebuilt Outline Server image: 100%", runner.command)
        self.assertNotIn(str(archive), runner.command)
        self.assertTrue(runner.calls[0][3])

    def test_rejects_missing_or_empty_archive_before_remote_call(self) -> None:
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            empty_archive = Path(directory) / "empty.tar.gz"
            empty_archive.touch()
            missing_archive = Path(directory) / "missing.tar.gz"

            with self.assertLogs("fck_rkn.agents.outline", level="ERROR"):
                self.assertEqual(load_outline_image(runner, empty_archive), 1)
                self.assertEqual(load_outline_image(runner, missing_archive), 1)

        self.assertEqual(runner.calls, [])

    def test_reports_failure_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.tar.gz"
            archive.write_bytes(b"archive")
            with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
                self.assertEqual(load_outline_image(FakeRunner(exit_code=5), archive), 5)
                self.assertEqual(
                    load_outline_image(
                        FakeRunner(error=RemoteTimeoutError()), archive
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
