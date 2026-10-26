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
        self.assertEqual(settings.EU_SERVER_ADDRESS, "192.0.2.10")
        self.assertEqual(settings.SSH_PORT, 2222)
        self.assertEqual(settings.EU_DNS_ADDRESSES, ["192.0.2.53", "2001:db8::53"])
        self.assertEqual(settings.EU_LOCAL_NETWORKS, ["192.0.2.0/24", "2001:db8:1::/64"])
        self.assertIsNone(settings.RU_METADATA_ADDRESS)
        self.assertIsNone(settings.RU_PROVIDER_NETWORKS)

    def test_eu_only_configuration_does_not_require_ru_settings(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID.replace("RU_SERVER_ADDRESS=192.0.2.20\n", ""),
            encoding="utf-8",
        )

        settings = load_eu_production_settings(self.config_path)

        self.assertIsNone(settings.RU_SERVER_ADDRESS)
        self.assertIsNone(settings.RU_DNS_ADDRESSES)
        self.assertIsNone(settings.RU_PROVIDER_NETWORKS)

    def test_loads_and_normalizes_all_ru_settings(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        complete = VALID + """\
RU_METADATA_ADDRESS=2001:0db8::1
RU_DNS_ADDRESSES=["192.0.2.54","2001:db8::54"]
RU_DHCP_SERVER_ADDRESS=192.0.2.3
RU_NTP_ADDRESSES=["192.0.2.124"]
RU_LOCAL_NETWORKS=["192.0.3.0/24","2001:db8:2::/64"]
RU_PROVIDER_NETWORKS=["198.51.100.0/24","2001:db8:abcd::/48"]
"""
        self.config_path.write_text(complete, encoding="utf-8")

        settings = load_eu_production_settings(self.config_path)

        self.assertEqual(settings.RU_METADATA_ADDRESS, "2001:db8::1")
        self.assertEqual(settings.RU_DNS_ADDRESSES, ["192.0.2.54", "2001:db8::54"])
        self.assertEqual(settings.RU_DHCP_SERVER_ADDRESS, "192.0.2.3")
        self.assertEqual(settings.RU_NTP_ADDRESSES, ["192.0.2.124"])
        self.assertEqual(settings.RU_LOCAL_NETWORKS, ["192.0.3.0/24", "2001:db8:2::/64"])
        self.assertEqual(
            settings.RU_PROVIDER_NETWORKS,
            ["198.51.100.0/24", "2001:db8:abcd::/48"],
        )

    def test_rejects_invalid_ru_addresses_lists_and_networks_without_values(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        invalid_variants = (
            ("RU_SERVER_ADDRESS=192.0.2.20", "RU_SERVER_ADDRESS=ru.example.com"),
            ("RU_METADATA_ADDRESS=192.0.2.1", "RU_METADATA_ADDRESS=not-an-ip-secret"),
            ('RU_DNS_ADDRESSES=["192.0.2.54"]', "RU_DNS_ADDRESSES=not-json-secret"),
            ('RU_PROVIDER_NETWORKS=["198.51.100.0/24"]', 'RU_PROVIDER_NETWORKS=["198.51.100.5/24"]'),
        )
        valid = VALID + """\
RU_METADATA_ADDRESS=192.0.2.1
RU_DNS_ADDRESSES=["192.0.2.54"]
RU_PROVIDER_NETWORKS=["198.51.100.0/24"]
"""
        for old, new in invalid_variants:
            with self.subTest(value=new):
                self.config_path.write_text(valid.replace(old, new), encoding="utf-8")
                with self.assertRaises(ProductionConfigError) as raised:
                    load_eu_production_settings(self.config_path)
                self.assertNotIn("secret", str(raised.exception))

    def test_optional_ru_template_values_are_blank_and_documented(self) -> None:
        template = PROD_TEMPLATE_FILE.read_text(encoding="utf-8")
        for name in (
            "RU_SERVER_ADDRESS",
            "RU_METADATA_ADDRESS",
            "RU_DNS_ADDRESSES",
            "RU_DHCP_SERVER_ADDRESS",
            "RU_NTP_ADDRESSES",
            "RU_LOCAL_NETWORKS",
            "RU_PROVIDER_NETWORKS",
        ):
            with self.subTest(name=name):
                self.assertRegex(template, rf"(?m)^{name}=$")
        self.assertNotIn("ss://", template)
        self.assertNotIn("PRIVATE KEY", template)

    def test_local_production_file_is_not_overridden_by_process_environment(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(VALID, encoding="utf-8")

        with patch.dict(os.environ, {"EU_SERVER_ADDRESS": "192.0.2.99"}):
            settings = load_eu_production_settings(self.config_path)

        self.assertEqual(settings.EU_SERVER_ADDRESS, "192.0.2.10")

    def test_defaults_ssh_port_to_22(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID.replace("SSH_PORT=2222\n", ""), encoding="utf-8"
        )

        self.assertEqual(load_eu_production_settings(self.config_path).SSH_PORT, 22)

    def test_rejects_missing_required_values_without_exposing_values(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            "EU_SERVER_ADDRESS=192.0.2.10\n", encoding="utf-8"
        )

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        self.assertIn("- SSH_USER: Field required", str(raised.exception))
        self.assertIn("- SSH_KEY_PATH: Field required", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_reports_multiple_validation_errors_without_exposing_values(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        invalid = VALID.replace(
            "EU_SERVER_ADDRESS=192.0.2.10",
            "EU_SERVER_ADDRESS=not-an-ip-secret",
        ).replace(
            'EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]',
            "EU_DNS_ADDRESSES=not-json-secret",
        ).replace(
            'EU_NTP_ADDRESSES=["192.0.2.123"]',
            "EU_NTP_ADDRESSES=not-json-ntp-secret",
        )
        self.config_path.write_text(invalid, encoding="utf-8")

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        message = str(raised.exception)
        self.assertIn("EU_SERVER_ADDRESS: must be an IPv4 or IPv6 address", message)
        self.assertIn(
            "EU_DNS_ADDRESSES: must be a JSON array of IPv4/IPv6 addresses",
            message,
        )
        self.assertIn(
            "EU_NTP_ADDRESSES: must be a JSON array of IPv4/IPv6 addresses",
            message,
        )
        self.assertNotIn("not-an-ip-secret", message)
        self.assertNotIn("not-json-secret", message)
        self.assertNotIn("not-json-ntp-secret", message)

    def test_reports_non_array_json_for_list_settings(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        invalid = VALID.replace(
            'EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]',
            'EU_DNS_ADDRESSES="not-an-array-secret"',
        )
        self.config_path.write_text(invalid, encoding="utf-8")

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        message = str(raised.exception)
        self.assertIn("EU_DNS_ADDRESSES", message)
        self.assertIn("JSON array of IPv4/IPv6 addresses", message)
        self.assertNotIn("not-an-array-secret", message)

    def test_reports_unreplaced_placeholders_in_json_list_settings(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        invalid = VALID.replace(
            'EU_DNS_ADDRESSES=["192.0.2.53","2001:db8::53"]',
            'EU_DNS_ADDRESSES=["SET_TO_DNS_SECRET"]',
        )
        self.config_path.write_text(invalid, encoding="utf-8")

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        message = str(raised.exception)
        self.assertIn(
            "EU_DNS_ADDRESSES: contains a placeholder that must be replaced",
            message,
        )
        self.assertNotIn("SET_TO_DNS_SECRET", message)

    def test_rejects_template_placeholders(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text(
            VALID.replace(
                "EU_SERVER_ADDRESS=192.0.2.10",
                "EU_SERVER_ADDRESS=SET_TO_ADDRESS",
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ProductionConfigError) as raised:
            load_eu_production_settings(self.config_path)

        self.assertIn(
            "EU_SERVER_ADDRESS: must be replaced with an operator-supplied value",
            str(raised.exception),
        )
        self.assertNotIn("SET_TO_ADDRESS", str(raised.exception))

    def test_eu_server_address_accepts_ipv4_and_normalizes_ipv6(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        for raw, expected in (
            ("192.0.2.10", "192.0.2.10"),
            ("2001:0db8:0:0:0:0:0:10", "2001:db8::10"),
        ):
            with self.subTest(address=raw):
                self.config_path.write_text(
                    VALID.replace("EU_SERVER_ADDRESS=192.0.2.10", f"EU_SERVER_ADDRESS={raw}"),
                    encoding="utf-8",
                )
                settings = load_eu_production_settings(self.config_path)
                self.assertEqual(settings.EU_SERVER_ADDRESS, expected)

    def test_eu_server_address_rejects_malformed_and_missing_values(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        for label, replacement in (
            ("dns name", "EU_SERVER_ADDRESS=eu.example.com"),
            ("octet out of range", "EU_SERVER_ADDRESS=192.0.2.300"),
            ("with port", "EU_SERVER_ADDRESS=192.0.2.10:22"),
            ("empty", "EU_SERVER_ADDRESS="),
            ("missing", ""),
        ):
            with self.subTest(case=label):
                self.config_path.write_text(
                    VALID.replace("EU_SERVER_ADDRESS=192.0.2.10", replacement),
                    encoding="utf-8",
                )
                with self.assertRaises(ProductionConfigError) as caught:
                    load_eu_production_settings(self.config_path)
                self.assertIn("EU_SERVER_ADDRESS", str(caught.exception))

    def test_rejects_invalid_addresses_networks_and_ssh_port(self) -> None:
        invalid_variants = (
            ("EU_SERVER_ADDRESS=192.0.2.10", "EU_SERVER_ADDRESS=not-an-ip"),
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

        self.assertEqual(raised.exception.line, 12)
        self.assertIn("prod.env at line 12", str(raised.exception))
        self.assertNotIn("super-secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
