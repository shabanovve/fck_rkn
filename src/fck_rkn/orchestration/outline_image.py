"""Build the patched Outline Server image once on the orchestrator host."""

from __future__ import annotations

import gzip
import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from fck_rkn.agents.outline import (
    OUTLINE_BASE_IMAGE,
    OUTLINE_IMAGE,
    OUTLINE_SS_SERVER_VERSION,
)
from fck_rkn.remote import render


logger = logging.getLogger(__name__)
INSPECT_TIMEOUT_SECONDS = 60
BUILD_TIMEOUT_SECONDS = 900

LocalRun = Callable[..., subprocess.CompletedProcess]


def _docker(
    run: LocalRun, args: list[str], timeout: int, stdin: str | None = None
) -> int:
    try:
        return run(
            ["docker", *args],
            input=stdin,
            text=True,
            timeout=timeout,
            check=False,
            capture_output=True,
        ).returncode
    except subprocess.TimeoutExpired:
        logger.error("docker %s exceeded the %d second timeout", args[0], timeout)
        return 1
    except OSError as error:
        logger.error("Could not run docker on the local host: %s", error)
        return 1


def build_outline_image(run: LocalRun = subprocess.run) -> int:
    """Build the patched image locally unless it already exists."""
    if _docker(run, ["image", "inspect", OUTLINE_IMAGE], INSPECT_TIMEOUT_SECONDS) == 0:
        logger.info("Reusing the existing local Outline Server image")
        return 0
    logger.info("Building the Outline Server image locally")
    dockerfile = render(
        "outline_shadowbox.Dockerfile",
        ss_server_version=OUTLINE_SS_SERVER_VERSION,
        base_image=OUTLINE_BASE_IMAGE,
    )
    # The Dockerfile is the whole context: a snap-confined docker cannot read
    # temporary directories.
    result = _docker(
        run, ["build", "-t", OUTLINE_IMAGE, "-"], BUILD_TIMEOUT_SECONDS, dockerfile
    )
    if result != 0:
        logger.error("Building the Outline Server image failed")
    return result


def export_outline_image(
    directory: Path, popen: Callable[..., subprocess.Popen] = subprocess.Popen
) -> Path | None:
    """Stream the local image through gzip into an archive in directory."""
    archive = directory / "outline-image.tar.gz"
    # A pipe is required: a snap-confined docker cannot write to a file stdout.
    try:
        with popen(
            ["docker", "save", OUTLINE_IMAGE],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as target:
                shutil.copyfileobj(process.stdout, target)
            saved = process.wait(timeout=BUILD_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.error("Saving the Outline Server image failed: %s", error)
        archive.unlink(missing_ok=True)
        return None
    if saved != 0:
        logger.error("Saving the Outline Server image failed")
        archive.unlink(missing_ok=True)
        return None
    return archive
