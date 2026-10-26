"""Operator-local protected storage for production secrets (never in the repo)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fck_rkn.production_config import REPOSITORY_ROOT


class SecretStoreError(Exception):
    """The secret could not be stored or read safely; the message holds no secret."""


def default_secret_directory() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fck_rkn" / "secrets"


class LocalSecretStore:
    """Files with mode 0600 inside a 0700 directory outside the repository."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or default_secret_directory()).resolve()
        if self.directory.is_relative_to(REPOSITORY_ROOT):
            raise SecretStoreError(
                "The secret directory must be outside the repository"
            )

    def _path(self, name: str) -> Path:
        if not name or Path(name).name != name or name.startswith("."):
            raise SecretStoreError("Invalid secret name")
        return self.directory / name

    def write(self, name: str, value: str) -> None:
        """Atomically store value, replacing any previous value for name."""
        path = self._path(name)
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".tmp-")
        except OSError as error:
            raise SecretStoreError(
                f"Could not prepare the secret directory: {error.strerror}"
            ) from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(value)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as error:
            Path(temporary).unlink(missing_ok=True)
            raise SecretStoreError(
                f"Could not store the secret: {error.strerror}"
            ) from None

    def read(self, name: str) -> str | None:
        """Return the stored value, or None if absent; refuse unsafe permissions."""
        path = self._path(name)
        try:
            status = path.stat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SecretStoreError(
                f"Could not read the secret: {error.strerror}"
            ) from None
        if status.st_mode & 0o077:
            raise SecretStoreError(
                "The stored secret is accessible to other users; fix its mode to 0600"
            )
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise SecretStoreError("Could not read the secret") from None
