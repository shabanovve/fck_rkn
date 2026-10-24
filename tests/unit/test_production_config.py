from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fck_rkn.production_config import (
    PROD_TEMPLATE_FILE,
    EuProductionSettings,
    ProductionConfigError,
    ProductionDotenvSyntaxError,
    ensure_prod_config,
    load_eu_production_settings,
)


VALID = """\
EU_SERVER_HOST=eu.example.net
EU_SERVER_ADDRESS=192.0.2.10
RU_SERVER_ADDRESS=192.0.2.20
SSH_USER=deploy
SSH_KEY_PATH=/run/secrets/fck-rkn/deploy_key
SSH_PORT=2222
EU_METADATA_ADDRESS=192.0.2.1
EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]
EU_DHCP_SERVER_ADDRESS=192.0.2.2
EU_NTP_ADDRESSES=["192.0.2.123"]
EU_LOCAL_NETWORKS=["192.0.2.0/24","2001:db8:1::/64"]
"""


class ProductionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.config_path = self.directory / "config" / "prod.env"

    def test_creates_production_config_with_restricted_permissions(self) -> None:
        self.assertTrue(ensure_prod_config(self.config_path, PROD_TEMPLATE_FILE))

        self.assertEqual(
            self.config_path.read_text(encoding="utf-8"),
            PROD_TEMPLATE_FILE.read_text(encoding="utf-8"),
        )
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(ensure_prod_config(self.config_path, PROD_TEMPLATE_FILE))

    def test_existing_operator_config_is_not_modified(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(VALID, encoding="utf-8")

        self.assertFalse(ensure_prod_config(self.config_path, PROD_TEMPLATE_FILE))
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), VALID)

    def test_failed_template_copy_leaves_no_partial_file(self) -> None:
        with patch("fck_rkn.production_config.os.link", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                ensure_prod_config(self.config_path, PROD_TEMPLATE_FILE)

        self.assertFalse(self.config_path.exists())
        self.assertEqual(list(self.config_path.parent.glob(".prod.env.*")), [])

    def test_does_not_replace_a_config_created_concurrently(self) -> None:
        original = VALID

        def link_then_raise(source: Path, destination: Path) -> None:
            destination.write_text(original, encoding="utf-8")
            raise FileExistsError

        with patch("fck_rkn.production_config.os.link", side_effect=link_then_raise):
            self.assertFalse(ensure_prod_config(self.config_path, PROD_TEMPLATE_FILE))

        self.assertEqual(self.config_path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.config_path.parent.glob(".prod.env.*")), [])

    def test_loads_and_normalizes_eu_production_settings(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(VALID, encoding="utf-8")

        settings = load_eu_production_settings(self.config_path)

        self.assertIsInstance(settings, EuProductionSettings)
        self.assertEqual(settings.EU_SERVER_HOST, "eu.example.net")
        self.assertEqual(settings.EU_SERVER_ADDRESS, "192.0.2.10")
        self.assertEqual(settings.SSH_PORT, 2222)
        self.assertEqual(settings.EU_DNS_ADDRESSES, ["192.0.2.53", "2001:db8::53"])
        self.assertEqual(settings.EU_LOCAL_NETWORKS, ["192.0.2.0/24", "2001:db8:1::/64"])

    def test_local_production_file_is_not_overridden_by_process_environment(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(VALID, encoding="utf-8")

        with patch.dict(os.environ, {"EU_SERVER_HOST": "attacker.example"}):
            settings = load_eu_production_settings(self.config_path)

        self.assertEqual(settings.EU_SERVER_HOST, "eu.example.net")

    def test_defaults_ssh_port_to_22(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID.replace("SSH_PORT=2222\n", ""), encoding="utf-8"
        )

        self.assertEqual(load_eu_production_settings(self.config_path).SSH_PORT, 22)

    def test_rejects_missing_required_values_without_exposing_values(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("EU_SERVER_HOST=secret-value\n", encoding="utf-8")

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        self.assertIn("SSH_USER", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_rejects_template_placeholders(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID.replace("EU_SERVER_HOST=eu.example.net", "EU_SERVER_HOST=SET_TO_HOST"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProductionConfigError, "EU_SERVER_HOST"):
            load_eu_production_settings(self.config_path)

    def test_rejects_invalid_addresses_networks_and_ssh_port(self) -> None:
        invalid_variants = (
            ("EU_SERVER_HOST=eu.example.net", "EU_SERVER_HOST=bad host"),
            ("EU_SERVER_ADDRESS=192.0.2.10", "EU_SERVER_ADDRESS=not-ip"),
            ("EU_DNS_ADDRESSES=[\"192.0.2.53\",\"2001:db8::53\"]", "EU_DNS_ADDRESSES=[]"),
            ("EU_NTP_ADDRESSES=[\"192.0.2.123\"]", "EU_NTP_ADDRESSES=[\"ntp.example\"]"),
            ("EU_LOCAL_NETWORKS=[\"192.0.2.0/24\",\"2001:db8:1::/64\"]", "EU_LOCAL_NETWORKS=[\"192.0.2.1/24\"]"),
            ("SSH_PORT=2222", "SSH_PORT=70000"),
        )
        for old, new in invalid_variants:
            with self.subTest(value=new):
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(VALID.replace(old, new), encoding="utf-8")
                with self.assertRaises(ProductionConfigError):
                    load_eu_production_settings(self.config_path)

    def test_reports_dotenv_syntax_error_without_line_contents(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID + "DO_NOT_LEAK=super-secret-value\nBAD_LINE='unterminated\n",
            encoding="utf-8",
        )

        with self.assertRaises(ProductionDotenvSyntaxError) as raised:
            load_eu_production_settings(self.config_path)

        self.assertEqual(raised.exception.line, 13)
        self.assertNotIn("super-secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
