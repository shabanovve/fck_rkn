from __future__ import annotations

import gzip
import subprocess
import tempfile
import unittest
from pathlib import Path

from fck_rkn.agents.outline import OUTLINE_BASE_IMAGE, OUTLINE_IMAGE
from fck_rkn.orchestration.outline_image import (
    build_outline_image,
    export_outline_image,
)


class FakeDocker:
    def __init__(self, *codes: int, error: Exception | None = None) -> None:
        self.codes = list(codes)
        self.error = error
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get("input")))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, self.codes.pop(0))


class BuildOutlineImageTests(unittest.TestCase):
    def test_reuses_an_existing_local_image_without_building(self) -> None:
        docker = FakeDocker(0)
        self.assertEqual(build_outline_image(docker), 0)
        self.assertEqual(
            [call[0] for call in docker.calls],
            [["docker", "image", "inspect", OUTLINE_IMAGE]],
        )

    def test_builds_once_from_the_patched_dockerfile_when_missing(self) -> None:
        docker = FakeDocker(1, 0)
        self.assertEqual(build_outline_image(docker), 0)
        build_argv, dockerfile = docker.calls[1]
        self.assertEqual(build_argv, ["docker", "build", "-t", OUTLINE_IMAGE, "-"])
        self.assertIn("return nil", dockerfile)
        self.assertIn(OUTLINE_BASE_IMAGE, dockerfile)
        self.assertEqual(len(docker.calls), 2)

    def test_build_failure_returns_nonzero(self) -> None:
        with self.assertLogs("fck_rkn.orchestration.outline_image", "ERROR"):
            self.assertEqual(build_outline_image(FakeDocker(1, 3)), 3)

    def test_missing_docker_and_timeout_return_nonzero(self) -> None:
        with self.assertLogs("fck_rkn.orchestration.outline_image", "ERROR"):
            self.assertEqual(
                build_outline_image(FakeDocker(error=FileNotFoundError("docker"))), 1
            )
            self.assertEqual(
                build_outline_image(
                    FakeDocker(error=subprocess.TimeoutExpired("docker", 1))
                ),
                1,
            )


class ExportOutlineImageTests(unittest.TestCase):
    def test_saves_and_compresses_the_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def docker(argv, **kwargs):
                kwargs["stdout"].write(b"image-bytes")
                return subprocess.CompletedProcess(argv, 0)

            archive = export_outline_image(Path(directory), docker)

            self.assertEqual(gzip.decompress(archive.read_bytes()), b"image-bytes")
            self.assertEqual(
                sorted(p.name for p in Path(directory).iterdir()),
                ["outline-image.tar.gz"],
            )

    def test_save_failure_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs("fck_rkn.orchestration.outline_image", "ERROR"):
                self.assertIsNone(
                    export_outline_image(Path(directory), FakeDocker(1))
                )


if __name__ == "__main__":
    unittest.main()
