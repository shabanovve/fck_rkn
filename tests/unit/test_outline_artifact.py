from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from fck_rkn.orchestration import outline_artifact
from fck_rkn.orchestration.outline_artifact import (
    build_outline_artifact,
    read_approved_digest,
    retrieve_outline_artifact,
)


class RetrieveOutlineArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory))
        self.archive = self.directory / "image.tar.gz"
        self.archive.write_bytes(b"approved image bytes")
        self.digest_file = self.directory / "outline-image.sha256"
        self.digest = hashlib.sha256(b"approved image bytes").hexdigest()

    def retrieve(self):
        with self.assertLogs(outline_artifact.logger, level="INFO") as logs:
            result = retrieve_outline_artifact(self.archive, self.digest_file)
        return result, "\n".join(logs.output)

    def test_valid_artifact_is_returned(self) -> None:
        self.digest_file.write_text(self.digest + "\n")

        result, _ = self.retrieve()

        self.assertEqual(result, self.archive)

    def test_missing_archive_is_rejected(self) -> None:
        self.digest_file.write_text(self.digest)
        self.archive.unlink()

        result, logs = self.retrieve()

        self.assertIsNone(result)
        self.assertIn("missing", logs)

    def test_digest_mismatch_is_rejected(self) -> None:
        self.digest_file.write_text(hashlib.sha256(b"other").hexdigest())

        result, logs = self.retrieve()

        self.assertIsNone(result)
        self.assertIn("does not match", logs)

    def test_missing_or_malformed_digest_fails_closed(self) -> None:
        for label, content in (
            ("missing", None),
            ("empty", ""),
            ("uppercase", self.digest.upper()),
            ("short", self.digest[:10]),
            ("tag name", "latest"),
        ):
            with self.subTest(case=label):
                self.digest_file.unlink(missing_ok=True)
                if content is not None:
                    self.digest_file.write_text(content)

                result, _ = self.retrieve()

                self.assertIsNone(result)

    def test_read_approved_digest_accepts_one_hex_line(self) -> None:
        self.digest_file.write_text(self.digest + "\n")
        with self.assertNoLogs(outline_artifact.logger, level="ERROR"):
            self.assertEqual(read_approved_digest(self.digest_file), self.digest)

    def test_shipped_digest_path_and_archive_location(self) -> None:
        self.assertEqual(
            outline_artifact.APPROVED_DIGEST_FILE.name, "outline-image.sha256"
        )
        self.assertEqual(outline_artifact.APPROVED_DIGEST_FILE.parent.name, "artifacts")
        self.assertEqual(outline_artifact.ARTIFACT_FILE.parent.parent.name, "config")


class BuildOutlineArtifactTests(unittest.TestCase):
    def test_builds_and_stores_the_archive_without_approving_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifacts" / "outline-image.tar.gz"

            def export(target: Path) -> Path:
                path = target / "outline-image.tar.gz"
                path.write_bytes(b"built")
                return path

            with self.assertLogs(outline_artifact.logger, level="INFO") as logs:
                result = build_outline_artifact(archive, lambda: 0, export)

            self.assertEqual(result, 0)
            self.assertEqual(archive.read_bytes(), b"built")
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertIn(hashlib.sha256(b"built").hexdigest(), "\n".join(logs.output))
            self.assertEqual([p.name for p in archive.parent.iterdir()], [archive.name])

    def test_build_or_export_failure_leaves_no_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "out" / "image.tar.gz"

            self.assertEqual(build_outline_artifact(archive, lambda: 1, lambda _: None), 1)
            self.assertEqual(build_outline_artifact(archive, lambda: 0, lambda _: None), 1)
            self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
