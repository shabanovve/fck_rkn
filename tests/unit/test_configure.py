from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fck_rkn.orchestration.configure import TEMPLATE_FILE, configure_test_env


class FakeGcp:
    def __init__(self) -> None:
        self.network = True
        self.subnets = {"default": "10.1.0.0/20"}
        self.image = True
        self.machine = True
        self.calls: list[tuple[str, ...]] = []

    def network_exists(self, project: str, network: str) -> bool:
        self.calls.append(("network", project, network))
        return self.network

    def list_subnets(self, project: str, zone: str, network: str) -> dict[str, str]:
        self.calls.append(("subnets", project, zone, network))
        return self.subnets

    def image_family_exists(self, project: str, family: str) -> bool:
        self.calls.append(("image", project, family))
        return self.image

    def machine_type_exists(self, project: str, zone: str, machine_type: str) -> bool:
        self.calls.append(("machine", project, zone, machine_type))
        return self.machine


class ConfigureTestEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "test.env"
        self.gcp = FakeGcp()

    def run_configure(self) -> list[str]:
        return configure_test_env(self.path, TEMPLATE_FILE, self.gcp)

    def test_creates_the_file_from_the_template_and_fills_the_cidr(self) -> None:
        report = self.run_configure()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("GCP_SUBNET_CIDR=10.1.0.0/20", text)
        self.assertNotIn("GCP_SUBNET_CIDR=10.166.0.0/20", text)
        self.assertIn("# Name of the existing VPC network", text)
        self.assertTrue(any("Created" in line for line in report))
        self.assertIn("Set GCP_SUBNET_CIDR=10.1.0.0/20", report)

    def test_checks_network_image_and_machine_type(self) -> None:
        self.run_configure()

        self.assertEqual(
            self.gcp.calls,
            [
                ("network", "fck-rkn-test", "default"),
                ("subnets", "fck-rkn-test", "europe-north1-a", "default"),
                ("image", "ubuntu-os-cloud", "ubuntu-2404-lts-amd64"),
                ("machine", "fck-rkn-test", "europe-north1-a", "e2-micro"),
            ],
        )

    def test_keeps_values_the_operator_already_set(self) -> None:
        self.gcp.subnets = {"mine": "192.168.0.0/24"}
        self.path.write_text(
            "GCP_PROJECT_ID=fck-rkn-test\nGCP_ZONE=europe-west1-b\n"
            "GCP_SUBNETWORK=mine\nGCP_SUBNET_CIDR=192.168.0.0/24\n"
            "GCP_MACHINE_TYPE=e2-small\n",
            encoding="utf-8",
        )

        self.run_configure()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("GCP_SUBNET_CIDR=192.168.0.0/24", text)
        self.assertIn("GCP_MACHINE_TYPE=e2-small", text)
        self.assertIn("GCP_ZONE=europe-west1-b", text)
        self.assertIn("GCP_SUBNETWORK=mine", text)

    def test_selects_the_only_subnetwork_when_the_configured_one_is_missing(
        self,
    ) -> None:
        self.gcp.subnets = {"only-one": "10.2.0.0/24"}

        report = self.run_configure()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("GCP_SUBNETWORK=only-one", text)
        self.assertIn("GCP_SUBNET_CIDR=10.2.0.0/24", text)
        self.assertIn("Set GCP_SUBNETWORK=only-one", report)

    def test_ambiguous_or_missing_subnetwork_is_an_error(self) -> None:
        for subnets in ({}, {"a": "10.0.0.0/24", "b": "10.0.1.0/24"}):
            with self.subTest(subnets=subnets):
                self.gcp.subnets = subnets
                with self.assertRaisesRegex(ValueError, "GCP_SUBNETWORK"):
                    self.run_configure()
                self.assertFalse(self.path.exists())

    def test_missing_gcp_resources_are_errors_and_write_nothing(self) -> None:
        for attribute in ("network", "image", "machine"):
            with self.subTest(attribute=attribute):
                gcp = FakeGcp()
                setattr(gcp, attribute, False)
                with self.assertRaises(ValueError):
                    configure_test_env(self.path, TEMPLATE_FILE, gcp)
                self.assertFalse(self.path.exists())

    def test_mismatched_cidr_is_reported_but_not_changed(self) -> None:
        self.path.write_text(
            "GCP_PROJECT_ID=fck-rkn-test\nGCP_SUBNET_CIDR=172.16.0.0/24\n",
            encoding="utf-8",
        )

        report = self.run_configure()

        self.assertIn("GCP_SUBNET_CIDR=172.16.0.0/24", self.path.read_text())
        self.assertTrue(any(line.startswith("Warning:") for line in report))

    def test_second_run_changes_nothing(self) -> None:
        self.run_configure()
        before = self.path.read_text(encoding="utf-8")

        self.assertEqual(self.run_configure(), [])

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_gcp_failure_keeps_an_existing_file(self) -> None:
        class Failing(FakeGcp):
            def network_exists(self, project: str, network: str) -> bool:
                raise RuntimeError("GCP unavailable")

        with self.assertRaises(RuntimeError):
            configure_test_env(self.path, TEMPLATE_FILE, Failing())
        self.assertFalse(self.path.exists())

        self.path.write_text("GCP_PROJECT_ID=fck-rkn-test\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            configure_test_env(self.path, TEMPLATE_FILE, Failing())
        self.assertEqual(self.path.read_text(), "GCP_PROJECT_ID=fck-rkn-test\n")

    def test_refuses_a_non_test_project(self) -> None:
        self.path.write_text("GCP_PROJECT_ID=fck-rkn-production\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.run_configure()
        self.assertEqual(self.gcp.calls, [])


if __name__ == "__main__":
    unittest.main()
