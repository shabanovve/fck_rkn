"""Reviewed production Outline image artifact: build for review and verify."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
from pathlib import Path

from fck_rkn.orchestration.outline_image import (
    build_outline_image,
    export_outline_image,
)
from fck_rkn.production_config import REPOSITORY_ROOT


logger = logging.getLogger(__name__)
APPROVED_DIGEST_FILE = REPOSITORY_ROOT / "artifacts" / "outline-image.sha256"
ARTIFACT_FILE = REPOSITORY_ROOT / "config" / "artifacts" / "outline-image.tar.gz"
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
READ_CHUNK_BYTES = 1024 * 1024


def read_approved_digest(path: Path = APPROVED_DIGEST_FILE) -> str | None:
    """Return the approved SHA-256 digest, or None if it is absent or malformed."""
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        logger.error(
            "No approved Outline image digest: %s is missing or unreadable", path
        )
        return None
    if not DIGEST_PATTERN.fullmatch(text):
        logger.error(
            "The approved Outline image digest in %s must be one line of 64 "
            "lowercase hex characters",
            path,
        )
        return None
    return text


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def retrieve_outline_artifact(
    archive: Path = ARTIFACT_FILE, digest_file: Path = APPROVED_DIGEST_FILE
) -> Path | None:
    """Return the approved archive only if its digest matches the approved one."""
    approved = read_approved_digest(digest_file)
    if approved is None:
        return None
    if not archive.is_file():
        logger.error(
            "The approved Outline image archive is missing; run "
            "'prod-env-host build-outline-artifact' and review it"
        )
        return None
    try:
        actual = sha256_of(archive)
    except OSError as error:
        logger.error("Could not read the Outline image archive: %s", error)
        return None
    if actual != approved:
        logger.error(
            "The Outline image archive does not match the approved digest; "
            "refusing to transfer it"
        )
        return None
    logger.info("The Outline image archive matches the approved digest")
    return archive


def build_outline_artifact(
    archive: Path = ARTIFACT_FILE, build=build_outline_image, export=export_outline_image
) -> int:
    """Build the image locally and store its archive for review (not approval)."""
    if build() != 0:
        return 1
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as directory:
        exported = export(Path(directory))
        if exported is None:
            return 1
        shutil.move(str(exported), archive)
    archive.chmod(0o600)
    logger.info(
        "Built %s with SHA-256 %s. Review it, then commit the digest to %s "
        "to approve it",
        archive,
        sha256_of(archive),
        APPROVED_DIGEST_FILE,
    )
    return 0
