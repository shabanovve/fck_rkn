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
        archive = Path(tempfile.gettempdir()) / "outline-image.tar.gz"

        self.assertEqual(load_outline_image(runner, archive), 0)

        self.assertEqual(runner.stdin, archive)
        self.assertIn(f"expected_image={OUTLINE_IMAGE}", runner.command)
        self.assertNotIn(str(archive), runner.command)
        self.assertTrue(runner.calls[0][3])

    def test_reports_failure_and_timeout(self) -> None:
        archive = Path("archive.tar.gz")
        with self.assertLogs("fck_rkn.remote.runner", level="ERROR"):
            self.assertEqual(load_outline_image(FakeRunner(exit_code=5), archive), 5)
            self.assertEqual(
                load_outline_image(FakeRunner(error=RemoteTimeoutError()), archive), 1
            )


if __name__ == "__main__":
    unittest.main()
