from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from fck_rkn.production_config import REPOSITORY_ROOT
from fck_rkn.production_secrets import LocalSecretStore, SecretStoreError


class LocalSecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name) / "secrets"
        self.store = LocalSecretStore(self.directory)

    def test_write_then_read_round_trips_with_private_modes(self) -> None:
        self.store.write("key", "ss://secret")

        self.assertEqual(self.store.read("key"), "ss://secret")
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / "key").stat().st_mode), 0o600)

    def test_missing_secret_reads_as_none(self) -> None:
        self.assertIsNone(self.store.read("absent"))

    def test_rewrite_replaces_the_value_and_leaves_no_temporary_files(self) -> None:
        self.store.write("key", "old")
        self.store.write("key", "new")

        self.assertEqual(self.store.read("key"), "new")
        self.assertEqual([p.name for p in self.directory.iterdir()], ["key"])

    def test_refuses_to_read_a_secret_readable_by_others(self) -> None:
        self.store.write("key", "secret-value")
        os.chmod(self.directory / "key", 0o644)

        with self.assertRaises(SecretStoreError) as context:
            self.store.read("key")

        self.assertNotIn("secret-value", str(context.exception))

    def test_rejects_path_like_secret_names(self) -> None:
        for name in ("", "../x", "a/b", ".hidden"):
            with self.subTest(name=name), self.assertRaises(SecretStoreError):
                self.store.write(name, "v")

    def test_directory_inside_the_repository_is_rejected(self) -> None:
        with self.assertRaises(SecretStoreError):
            LocalSecretStore(REPOSITORY_ROOT / "config" / "secrets")

    def test_write_failure_hides_the_value_and_leaves_no_file(self) -> None:
        self.directory.mkdir(mode=0o700)
        (self.directory / "key").mkdir()

        with self.assertRaises(SecretStoreError) as context:
            self.store.write("key", "secret-value")

        self.assertNotIn("secret-value", str(context.exception))
        self.assertEqual([p.name for p in self.directory.iterdir()], ["key"])

    def test_default_directory_follows_xdg_config_home_outside_the_repository(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as home:
            previous = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = home
            try:
                store = LocalSecretStore()
            finally:
                if previous is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = previous
            self.assertEqual(store.directory, (Path(home) / "fck_rkn" / "secrets").resolve())


if __name__ == "__main__":
    unittest.main()
