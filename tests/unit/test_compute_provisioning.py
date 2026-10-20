from __future__ import annotations

import ipaddress
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import compute_v1
from pydantic import ValidationError

from fck_rkn.config import (
    DotenvSyntaxError,
    TestEnvironmentSettings,
    load_config,
)
from fck_rkn.gcp.compute import (
    INSTANCE_NAME,
    NAT_CONFIG_NAME,
    NAT_ROUTER_NAME,
    _ensure_ssh_firewall_rule,
    _firewall_rule_matches,
    _firewall_rule_mismatches,
    _require_gcloud_cli,
    _wait_for_running,
    _wait_for_ssh,
    _validate_instance_configuration,
    build_eu_server,
    build_ru_server,
    build_cloud_router,
    build_ssh_firewall_rules,
    ensure_cloud_nat,
    ensure_eu_server,
    ensure_ru_server,
    lookup_ru_server,
    lookup_test_client,
)
from fck_rkn.orchestration.test_environment import main as orchestrator_main


def make_settings(**overrides: str) -> TestEnvironmentSettings:
    values = {
        "GCP_PROJECT_ID": "fck-rkn-test",
        "GCP_IAP_SOURCE_RANGE": "configured-iap-source-range",
    }
    values.update(overrides)
    return TestEnvironmentSettings.model_construct(**values)


class ComputeProvisioningTests(unittest.TestCase):
    def make_running_instance(self) -> compute_v1.Instance:
        instance = build_eu_server(make_settings())
        instance.status = "RUNNING"
        return instance

    def test_ru_server_lookup_returns_existing_instance_in_configured_network(
        self,
    ) -> None:
        settings = make_settings()
        instance = compute_v1.Instance(
            name="ru-server",
            network_interfaces=[
                compute_v1.NetworkInterface(
                    network="projects/fck-rkn-test/global/networks/default"
                )
            ],
        )
        instances_client = Mock()
        instances_client.get.return_value = instance

        result = lookup_ru_server(settings, instances_client)

        self.assertIs(result, instance)
        instances_client.get.assert_called_once_with(
            project="fck-rkn-test",
            zone="europe-north1-a",
            instance="ru-server",
        )
        instances_client.insert.assert_not_called()

    def test_ru_server_lookup_returns_none_when_instance_is_absent(self) -> None:
        instances_client = Mock()
        instances_client.get.side_effect = NotFound("instance missing")

        result = lookup_ru_server(make_settings(), instances_client)

        self.assertIsNone(result)
        instances_client.get.assert_called_once_with(
            project="fck-rkn-test",
            zone="europe-north1-a",
            instance="ru-server",
        )
        instances_client.insert.assert_not_called()

    def test_ru_server_lookup_rejects_instance_on_unconfigured_network(self) -> None:
        instance = compute_v1.Instance(
            name="ru-server",
            network_interfaces=[
                compute_v1.NetworkInterface(
                    network="projects/fck-rkn-test/global/networks/other"
                )
            ],
        )
        instances_client = Mock()
        instances_client.get.return_value = instance

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            lookup_ru_server(make_settings(), instances_client)

        instances_client.insert.assert_not_called()

    def test_test_client_lookup_returns_existing_instance(self) -> None:
        instance = compute_v1.Instance(
            name="test-client",
            network_interfaces=[
                compute_v1.NetworkInterface(
                    network="projects/fck-rkn-test/global/networks/default"
                )
            ],
        )
        instances_client = Mock()
        instances_client.get.return_value = instance

        result = lookup_test_client(make_settings(), instances_client)

        self.assertIs(result, instance)
        instances_client.get.assert_called_once_with(
            project="fck-rkn-test",
            zone="europe-north1-a",
            instance="test-client",
        )
        instances_client.insert.assert_not_called()

    def test_test_client_lookup_returns_none_when_instance_is_absent(self) -> None:
        instances_client = Mock()
        instances_client.get.side_effect = NotFound("instance missing")

        self.assertIsNone(lookup_test_client(make_settings(), instances_client))
        instances_client.insert.assert_not_called()

    def test_test_client_lookup_rejects_instance_on_unconfigured_network(
        self,
    ) -> None:
        instances_client = Mock()
        instances_client.get.return_value = compute_v1.Instance(
            name="test-client",
            network_interfaces=[
                compute_v1.NetworkInterface(
                    network="projects/fck-rkn-test/global/networks/other"
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "Existing test-client"):
            lookup_test_client(make_settings(), instances_client)

    def test_ru_server_creation_passes_expected_resource_to_gcp(self) -> None:
        settings = make_settings()
        created_instance = build_ru_server(settings)
        instances_client = Mock()
        instances_client.get.side_effect = [
            NotFound("instance missing"),
            created_instance,
        ]
        operation = Mock()
        instances_client.insert.return_value = operation

        with (
            patch("fck_rkn.gcp.compute._require_gcloud_cli") as require_gcloud,
            patch("fck_rkn.gcp.compute.ensure_cloud_nat") as ensure_nat,
            patch("fck_rkn.gcp.compute._ensure_ssh_firewall_rule") as ensure_fw,
            patch(
                "fck_rkn.gcp.compute._wait_for_running",
                return_value=created_instance,
            ) as wait_for_running,
            patch("fck_rkn.gcp.compute._wait_for_ssh") as wait_for_ssh,
        ):
            result = ensure_ru_server(
                settings,
                instances_client,
                firewalls_client=Mock(),
                routers_client=Mock(),
            )

        self.assertIs(result, created_instance)
        require_gcloud.assert_called_once_with()
        ensure_nat.assert_called_once()
        ensure_fw.assert_called_once()
        wait_for_running.assert_called_once_with(
            settings,
            instances_client,
            instance_name="ru-server",
        )
        wait_for_ssh.assert_called_once_with(
            settings,
            instance_name="ru-server",
        )
        operation.result.assert_called_once()
        instances_client.insert.assert_called_once()
        insert_args = instances_client.insert.call_args.kwargs
        self.assertEqual(insert_args["project"], "fck-rkn-test")
        self.assertEqual(insert_args["zone"], "europe-north1-a")
        resource = insert_args["instance_resource"]
        self.assertEqual(resource.name, "ru-server")
        self.assertEqual(
            resource.disks[0].initialize_params.source_image,
            "projects/ubuntu-os-cloud/global/images/family/"
            "ubuntu-2404-lts-amd64",
        )
        self.assertEqual(
            resource.network_interfaces[0].network,
            "projects/fck-rkn-test/global/networks/default",
        )
        self.assertEqual(
            resource.network_interfaces[0].subnetwork,
            "projects/fck-rkn-test/regions/europe-north1/subnetworks/default",
        )
        self.assertFalse(resource.network_interfaces[0].access_configs)

    def make_running_ru_instance(self) -> compute_v1.Instance:
        instance = build_ru_server(make_settings())
        instance.status = "RUNNING"
        return instance

    def test_ru_server_is_ready_after_running_and_ssh_checks(self) -> None:
        instance = self.make_running_ru_instance()
        instances_client = Mock()
        instances_client.get.return_value = instance
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with (
            patch("fck_rkn.gcp.compute._require_gcloud_cli"),
            patch("fck_rkn.gcp.compute.ensure_cloud_nat"),
            patch("fck_rkn.gcp.compute._ensure_ssh_firewall_rule"),
            patch(
                "fck_rkn.gcp.compute.subprocess.run", return_value=completed
            ) as run,
        ):
            result = ensure_ru_server(
                make_settings(),
                instances_client,
                firewalls_client=Mock(),
                routers_client=Mock(),
            )

        self.assertIs(result, instance)
        self.assertEqual(run.call_args.args[0][3], "ru-server")

    def test_ru_server_provisioning_failure_is_not_masked(self) -> None:
        instances_client = Mock()
        instances_client.get.side_effect = NotFound("instance missing")
        instances_client.insert.side_effect = RuntimeError("quota exceeded")

        with (
            patch("fck_rkn.gcp.compute._require_gcloud_cli"),
            patch("fck_rkn.gcp.compute.ensure_cloud_nat"),
            patch("fck_rkn.gcp.compute._ensure_ssh_firewall_rule"),
            patch("fck_rkn.gcp.compute._wait_for_ssh") as wait_for_ssh,
        ):
            with self.assertRaisesRegex(RuntimeError, "quota exceeded"):
                ensure_ru_server(
                    make_settings(),
                    instances_client,
                    firewalls_client=Mock(),
                    routers_client=Mock(),
                )

        wait_for_ssh.assert_not_called()

    def test_ru_server_running_wait_times_out(self) -> None:
        instance = build_ru_server(make_settings())
        instance.status = "PROVISIONING"
        instances_client = Mock()
        instances_client.get.return_value = instance

        with (
            patch("fck_rkn.gcp.compute.INSTANCE_READY_TIMEOUT_SECONDS", 0),
            self.assertRaisesRegex(TimeoutError, "ru-server did not reach RUNNING"),
        ):
            _wait_for_running(
                make_settings(), instances_client, instance_name="ru-server"
            )

    def test_ru_server_ssh_wait_times_out(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=255)
        with (
            patch("fck_rkn.gcp.compute.SSH_READY_TIMEOUT_SECONDS", 0),
            patch("fck_rkn.gcp.compute.subprocess.run", return_value=completed),
            self.assertRaisesRegex(TimeoutError, "authenticate to ru-server"),
        ):
            _wait_for_ssh(make_settings(), instance_name="ru-server")

    def test_instance_uses_approved_vm_image_network_and_os_login(self) -> None:
        instance = build_eu_server(make_settings())

        self.assertEqual(instance.name, INSTANCE_NAME)
        self.assertEqual(
            instance.machine_type,
            "zones/europe-north1-a/machineTypes/e2-micro",
        )
        self.assertEqual(
            instance.disks[0].initialize_params.source_image,
            "projects/ubuntu-os-cloud/global/images/family/"
            "ubuntu-2404-lts-amd64",
        )
        self.assertEqual(
            instance.network_interfaces[0].network,
            "projects/fck-rkn-test/global/networks/default",
        )
        self.assertEqual(
            instance.network_interfaces[0].subnetwork,
            "projects/fck-rkn-test/regions/europe-north1/subnetworks/default",
        )
        self.assertEqual(instance.metadata.items[0].key, "enable-oslogin")
        self.assertEqual(instance.metadata.items[0].value, "TRUE")
        self.assertEqual(instance.service_accounts, [])
        self.assertEqual(instance.tags.items, ["fck-rkn-test-eu-server-ssh"])
        self.assertFalse(instance.network_interfaces[0].access_configs)

    def test_firewall_rules_allow_iap_and_deny_other_ipv4(self) -> None:
        allow_rule, deny_rule = build_ssh_firewall_rules(make_settings())

        self.assertEqual(
            allow_rule.source_ranges,
            ["configured-iap-source-range"],
        )
        self.assertEqual(allow_rule.target_tags, ["fck-rkn-test-eu-server-ssh"])
        self.assertEqual(allow_rule.priority, 900)
        self.assertEqual(allow_rule.allowed[0].I_p_protocol, "tcp")
        self.assertEqual(allow_rule.allowed[0].ports, ["22"])
        self.assertEqual(len(deny_rule.source_ranges), 1)
        self.assertEqual(deny_rule.target_tags, allow_rule.target_tags)
        self.assertEqual(deny_rule.priority, 1000)
        self.assertEqual(len(deny_rule.source_ranges), 1)
        deny_range = ipaddress.ip_network(deny_rule.source_ranges[0])
        self.assertEqual(deny_range.prefixlen, 0)
        self.assertTrue(deny_range.network_address.is_unspecified)
        self.assertEqual(deny_rule.denied[0].I_p_protocol, "tcp")
        self.assertEqual(deny_rule.denied[0].ports, ["22"])

    def test_cloud_nat_targets_selected_subnet_with_auto_allocated_ips(self) -> None:
        router = build_cloud_router(make_settings())

        self.assertEqual(router.name, NAT_ROUTER_NAME)
        self.assertEqual(
            router.network,
            "projects/fck-rkn-test/global/networks/default",
        )
        self.assertEqual(len(router.nats), 1)
        nat = router.nats[0]
        self.assertEqual(nat.name, NAT_CONFIG_NAME)
        self.assertEqual(nat.type_, "PUBLIC")
        self.assertEqual(nat.nat_ip_allocate_option, "AUTO_ONLY")
        self.assertTrue(nat.enable_endpoint_independent_mapping)
        self.assertEqual(
            nat.source_subnetwork_ip_ranges_to_nat,
            "LIST_OF_SUBNETWORKS",
        )
        self.assertEqual(len(nat.subnetworks), 1)
        self.assertTrue(nat.subnetworks[0].name.endswith(
            "/regions/europe-north1/subnetworks/default"
        ))
        self.assertEqual(
            nat.subnetworks[0].source_ip_ranges_to_nat,
            ["ALL_IP_RANGES"],
        )
        self.assertFalse(nat.nat_ips)

    def test_creates_missing_cloud_router_with_nat(self) -> None:
        settings = make_settings()
        routers_client = Mock()
        routers_client.get.side_effect = [
            NotFound("router missing"),
            build_cloud_router(settings),
        ]
        operation = Mock()
        routers_client.insert.return_value = operation

        result = ensure_cloud_nat(settings, routers_client)

        self.assertEqual(result.name, NAT_ROUTER_NAME)
        operation.result.assert_called_once()
        created_router = routers_client.insert.call_args.kwargs["router_resource"]
        self.assertEqual(created_router.name, NAT_ROUTER_NAME)
        self.assertEqual(created_router.nats[0].name, NAT_CONFIG_NAME)

    def test_reuses_existing_matching_cloud_router(self) -> None:
        settings = make_settings()
        router = build_cloud_router(settings)
        router.network = (
            "https://www.googleapis.com/compute/v1/" + router.network
        )
        routers_client = Mock()
        routers_client.get.return_value = router

        result = ensure_cloud_nat(settings, routers_client)

        self.assertIs(result, router)
        routers_client.insert.assert_not_called()

    def test_reuses_cloud_router_created_by_concurrent_provisioning(self) -> None:
        settings = make_settings()
        router = build_cloud_router(settings)
        routers_client = Mock()
        routers_client.get.side_effect = [
            NotFound("router missing"),
            router,
        ]
        routers_client.insert.side_effect = AlreadyExists(
            "router was created concurrently"
        )

        result = ensure_cloud_nat(settings, routers_client)

        self.assertIs(result, router)
        routers_client.insert.assert_called_once()
        self.assertEqual(routers_client.get.call_count, 2)

    def test_rejects_existing_router_with_mismatched_nat_without_modifying(self) -> None:
        settings = make_settings()
        router = build_cloud_router(settings)
        router.nats[0].nat_ip_allocate_option = "MANUAL_ONLY"
        routers_client = Mock()
        routers_client.get.return_value = router

        with self.assertRaisesRegex(RuntimeError, "NAT IP allocation mode"):
            ensure_cloud_nat(settings, routers_client)

        routers_client.insert.assert_not_called()
        routers_client.patch.assert_not_called()

    def test_rejects_projects_other_than_isolated_test_project(self) -> None:
        with self.assertRaisesRegex(ValidationError, "isolated test project"):
            TestEnvironmentSettings(
                _env_file=None,
                GCP_PROJECT_ID="fck-rkn-production",
                GCP_IAP_SOURCE_RANGE="not-an-ip-cidr",
            )

    def test_rejects_invalid_iap_source_range_configuration(self) -> None:
        with self.assertRaisesRegex(ValidationError, "currently documented"):
            TestEnvironmentSettings(
                _env_file=None,
                GCP_PROJECT_ID="fck-rkn-test",
                GCP_IAP_SOURCE_RANGE="not-an-ip-cidr",
            )

    def test_dotenv_syntax_error_reports_line_without_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "test.env"
            config_path.write_text(
                "GCP_PROJECT_ID=fck-rkn-test\n"
                "GCP_IAP_SOURCE_RANGE='unterminated\n",
                encoding="utf-8",
            )

            with self.assertRaises(DotenvSyntaxError) as raised:
                load_config(config_path)

        self.assertEqual(raised.exception.line, 2)
        self.assertNotIn("unterminated", str(raised.exception))

    def test_missing_iap_setting_is_reported_as_missing_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "test.env"
            config_path.write_text(
                "GCP_PROJECT_ID=fck-rkn-test\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as raised:
                load_config(config_path)

        self.assertEqual(
            raised.exception.errors(include_input=False)[0]["loc"],
            ("GCP_IAP_SOURCE_RANGE",),
        )

    def test_cli_reports_bad_env_line_without_exposing_its_contents(self) -> None:
        output = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                side_effect=DotenvSyntaxError(
                    Path("config/test.env"),
                    11,
                ),
            ),
            redirect_stderr(output),
        ):
            result = orchestrator_main()

        self.assertEqual(result, 2)
        self.assertIn("Line: 11", output.getvalue())
        self.assertIn("KEY=value", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_cli_tells_operator_to_add_missing_iap_setting(self) -> None:
        output = StringIO()
        with (
            patch(
                "fck_rkn.orchestration.test_environment.load_config",
                side_effect=ValidationError.from_exception_data(
                    "TestEnvironmentSettings",
                    [
                        {
                            "type": "missing",
                            "loc": ("GCP_IAP_SOURCE_RANGE",),
                            "input": {},
                        }
                    ],
                ),
            ),
            redirect_stderr(output),
        ):
            result = orchestrator_main()

        self.assertEqual(result, 2)
        self.assertIn("GCP_IAP_SOURCE_RANGE", output.getvalue())
        self.assertIn("official IAP TCP forwarding documentation", output.getvalue())
        self.assertEqual(output.getvalue().count("GCP_IAP_SOURCE_RANGE"), 1)

    def test_reuses_existing_instance_without_inserting_duplicate(self) -> None:
        settings = make_settings()
        instance = self.make_running_instance()
        instances_client = Mock()
        instances_client.get.return_value = instance

        with (
            patch("fck_rkn.gcp.compute.ensure_cloud_nat"),
            patch("fck_rkn.gcp.compute._ensure_ssh_firewall_rule"),
            patch("fck_rkn.gcp.compute._wait_for_ssh"),
            patch("fck_rkn.gcp.compute._require_gcloud_cli"),
        ):
            with self.assertLogs("fck_rkn.gcp.compute", level="INFO") as logs:
                result = ensure_eu_server(
                    settings,
                    instances_client=instances_client,
                    firewalls_client=Mock(),
                    routers_client=Mock(),
                )

        self.assertIs(result, instance)
        instances_client.insert.assert_not_called()
        self.assertEqual(instances_client.get.call_count, 2)
        self.assertIn(
            "Starting eu-server provisioning",
            "\n".join(logs.output),
        )
        self.assertIn(
            "eu-server is provisioned, running, and SSH-ready",
            "\n".join(logs.output),
        )

    def test_creates_missing_scoped_firewall_rules(self) -> None:
        settings = make_settings()
        firewalls_client = Mock()
        firewalls_client.get.side_effect = [
            NotFound("allow rule missing"),
            NotFound("deny rule missing"),
        ]
        operation = Mock()
        firewalls_client.insert.return_value = operation

        _ensure_ssh_firewall_rule(settings, firewalls_client)

        self.assertEqual(firewalls_client.insert.call_count, 2)
        inserted_rules = [
            call.kwargs["firewall_resource"]
            for call in firewalls_client.insert.call_args_list
        ]
        self.assertEqual(
            [rule.name for rule in inserted_rules],
            [
                settings.GCP_SSH_FIREWALL_RULE,
                f"{settings.GCP_SSH_FIREWALL_RULE}-deny",
            ],
        )
        self.assertEqual(operation.result.call_count, 2)

    def test_creates_missing_instance_and_waits_for_ssh(self) -> None:
        settings = make_settings()
        instance = self.make_running_instance()
        instances_client = Mock()
        instances_client.get.side_effect = [
            NotFound("instance missing"),
            instance,
            instance,
        ]
        operation = Mock()
        instances_client.insert.return_value = operation

        with (
            patch("fck_rkn.gcp.compute.ensure_cloud_nat"),
            patch("fck_rkn.gcp.compute._ensure_ssh_firewall_rule"),
            patch("fck_rkn.gcp.compute._wait_for_ssh") as wait_for_ssh,
            patch("fck_rkn.gcp.compute._require_gcloud_cli"),
        ):
            result = ensure_eu_server(
                settings,
                instances_client=instances_client,
                firewalls_client=Mock(),
                routers_client=Mock(),
            )

        self.assertIs(result, instance)
        operation.result.assert_called_once()
        created_instance = instances_client.insert.call_args.kwargs[
            "instance_resource"
        ]
        self.assertEqual(created_instance.name, INSTANCE_NAME)
        wait_for_ssh.assert_called_once_with(settings, instance_name=INSTANCE_NAME)

    def test_ensures_cloud_nat_before_firewall_and_vm(self) -> None:
        settings = make_settings()
        instance = self.make_running_instance()
        order: list[str] = []
        with (
            patch("fck_rkn.gcp.compute._require_gcloud_cli"),
            patch(
                "fck_rkn.gcp.compute.ensure_cloud_nat",
                side_effect=lambda *_: order.append("nat"),
            ),
            patch(
                "fck_rkn.gcp.compute._ensure_ssh_firewall_rule",
                side_effect=lambda *_: order.append("firewall"),
            ),
            patch(
                "fck_rkn.gcp.compute._get_or_create_instance",
                side_effect=lambda *_: order.append("vm") or instance,
            ),
            patch("fck_rkn.gcp.compute._validate_instance_configuration"),
            patch(
                "fck_rkn.gcp.compute._wait_for_running",
                return_value=instance,
            ),
            patch("fck_rkn.gcp.compute._wait_for_ssh"),
        ):
            ensure_eu_server(
                settings,
                instances_client=Mock(),
                firewalls_client=Mock(),
                routers_client=Mock(),
            )

        self.assertEqual(order, ["nat", "firewall", "vm"])

    def test_requires_gcloud_before_making_cloud_changes(self) -> None:
        instances_client = Mock()
        firewalls_client = Mock()
        with patch(
            "fck_rkn.gcp.compute.shutil.which",
            return_value=None,
        ):
            with self.assertRaisesRegex(FileNotFoundError, "install gcloud"):
                ensure_eu_server(
                    make_settings(),
                    instances_client=instances_client,
                    firewalls_client=firewalls_client,
                    routers_client=Mock(),
                )

        firewalls_client.get.assert_not_called()
        instances_client.get.assert_not_called()
        instances_client.insert.assert_not_called()

    def test_requires_an_active_gcloud_account(self) -> None:
        with (
            patch(
                "fck_rkn.gcp.compute.shutil.which",
                return_value="/usr/bin/gcloud",
            ),
            patch(
                "fck_rkn.gcp.compute.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "gcloud auth login"):
                _require_gcloud_cli()

    def test_reports_timeout_while_checking_gcloud_account(self) -> None:
        with (
            patch(
                "fck_rkn.gcp.compute.shutil.which",
                return_value="/usr/bin/gcloud",
            ),
            patch(
                "fck_rkn.gcp.compute.subprocess.run",
                side_effect=subprocess.TimeoutExpired("gcloud auth list", 30),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                _require_gcloud_cli()

    def test_refuses_same_named_vm_with_unapproved_network_configuration(self) -> None:
        instance = self.make_running_instance()
        instance.tags.items = ["unexpected"]

        with self.assertRaisesRegex(RuntimeError, "lacks the dedicated SSH"):
            _validate_instance_configuration(instance, make_settings())

    def test_refuses_existing_vm_with_external_ip(self) -> None:
        instance = self.make_running_instance()
        instance.network_interfaces[0].access_configs.append(
            compute_v1.AccessConfig(name="External NAT")
        )

        with self.assertRaisesRegex(RuntimeError, "without external IPs"):
            _validate_instance_configuration(instance, make_settings())

    def test_refuses_existing_vm_with_external_ipv6(self) -> None:
        instance = self.make_running_instance()
        instance.network_interfaces[0].ipv6_access_type = "EXTERNAL"

        with self.assertRaisesRegex(RuntimeError, "without external IPs"):
            _validate_instance_configuration(instance, make_settings())

    def test_ssh_readiness_uses_iap_tunnel_and_os_login(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch(
            "fck_rkn.gcp.compute.subprocess.run",
            return_value=completed,
        ) as run:
            _wait_for_ssh(make_settings())

        command = run.call_args.args[0]
        self.assertEqual(
            command[:3],
            ["gcloud", "compute", "ssh"],
        )
        self.assertIn("--project=fck-rkn-test", command)
        self.assertIn("--zone=europe-north1-a", command)
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn("--command=true", command)

    def test_rejects_existing_firewall_rule_with_unapproved_settings(self) -> None:
        settings = make_settings()
        desired_allow, _ = build_ssh_firewall_rules(settings)
        mismatched = compute_v1.Firewall(
            name=desired_allow.name,
            network=desired_allow.network,
            direction="INGRESS",
            priority=900,
            source_ranges=["different-configured-range"],
            target_tags=list(desired_allow.target_tags),
            allowed=[
                compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])
            ],
        )

        self.assertFalse(_firewall_rule_matches(mismatched, desired_allow))

    def test_accepts_canonical_gcp_network_url_in_existing_rule(self) -> None:
        settings = make_settings()
        desired_allow, _ = build_ssh_firewall_rules(settings)
        existing = compute_v1.Firewall(
            name=desired_allow.name,
            network=(
                "https://www.googleapis.com/compute/v1/"
                + desired_allow.network
            ),
            direction=desired_allow.direction,
            priority=desired_allow.priority,
            source_ranges=list(desired_allow.source_ranges),
            target_tags=list(desired_allow.target_tags),
            allowed=list(desired_allow.allowed),
        )

        self.assertTrue(_firewall_rule_matches(existing, desired_allow))

    def test_reports_mismatched_firewall_fields_without_values(self) -> None:
        settings = make_settings()
        desired_allow, _ = build_ssh_firewall_rules(settings)
        existing = compute_v1.Firewall(
            name=desired_allow.name,
            network="different-network",
            direction=desired_allow.direction,
            priority=desired_allow.priority,
            source_ranges=["different-source-range"],
            target_tags=list(desired_allow.target_tags),
            allowed=list(desired_allow.allowed),
        )

        with self.assertLogs("fck_rkn.gcp.compute", level="INFO") as logs:
            mismatches = _firewall_rule_mismatches(existing, desired_allow)

        self.assertEqual(mismatches, ["network", "source_ranges"])
        self.assertNotIn("different-source-range", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()


class StopVmsTests(unittest.TestCase):
    def test_stops_only_running_vms_in_all_zones_and_waits(self) -> None:
        from fck_rkn.gcp.compute import stop_test_project_vms

        def scoped(*instances: tuple[str, str]) -> Mock:
            vms = []
            for name, status in instances:
                vm = Mock(status=status)
                vm.name = name
                vms.append(vm)
            return Mock(instances=vms)

        client = Mock()
        client.aggregated_list.return_value = [
            ("zones/europe-west1-b", scoped(("a", "RUNNING"), ("b", "TERMINATED"))),
            ("zones/us-central1-a", scoped(("c", "RUNNING"))),
        ]
        operations = [Mock(), Mock()]
        client.stop.side_effect = operations
        settings = make_settings()
        with (
            patch("fck_rkn.gcp.compute.google.auth.default", return_value=(Mock(), None)),
            patch("fck_rkn.gcp.compute.compute_v1.InstancesClient", return_value=client),
        ):
            stopped = stop_test_project_vms(settings)

        self.assertEqual(stopped, ["a", "c"])
        client.aggregated_list.assert_called_once_with(project=settings.GCP_PROJECT_ID)
        self.assertEqual(client.stop.call_count, 2)
        client.delete.assert_not_called()
        for operation in operations:
            operation.result.assert_called_once()
