"""Compute Engine operations used by the test environment."""

from __future__ import annotations

import ipaddress
import logging
import shutil
import subprocess
import time

import google.auth
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import compute_v1

from fck_rkn.config import TestEnvironmentSettings


INSTANCE_NAME = "eu-server"
RU_INSTANCE_NAME = "ru-server"
NAT_ROUTER_NAME = "fck-rkn-test-nat-router"
NAT_CONFIG_NAME = "fck-rkn-test-cloud-nat"
INSTANCE_OPERATION_TIMEOUT_SECONDS = 300
INSTANCE_READY_TIMEOUT_SECONDS = 600
SSH_READY_TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 10
SSH_COMMAND_TIMEOUT_SECONDS = 30
logger = logging.getLogger(__name__)


def _require_gcloud_cli() -> None:
    """Check the local OS Login client is installed and has an active account."""
    logger.info("Checking Google Cloud CLI installation and active account")
    if shutil.which("gcloud") is None:
        raise FileNotFoundError(
            "Google Cloud CLI is required for OS Login SSH readiness; "
            "install gcloud before provisioning"
        )
    try:
        result = subprocess.run(
            [
                "gcloud",
                "auth",
                "list",
                "--filter=status:ACTIVE",
                "--format=value(account)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Timed out while checking the active gcloud account"
        ) from error
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "No active gcloud account found. Run `gcloud auth login` "
            "before provisioning"
        )
    logger.info("Google Cloud CLI is installed and has an active account")


def validate_project_access(settings: TestEnvironmentSettings) -> None:
    """Verify ADC credentials can read the configured Compute Engine project."""
    logger.info(
        "Checking ADC access to the test project %s",
        settings.GCP_PROJECT_ID,
    )
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    compute_v1.ProjectsClient(credentials=credentials).get(
        project=settings.GCP_PROJECT_ID
    )
    logger.info("ADC can access the configured test project")


def stop_test_project_vms(settings: TestEnvironmentSettings) -> list[str]:
    """Stop every RUNNING VM in all zones of the test project; return stopped names."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    client = compute_v1.InstancesClient(credentials=credentials)
    pending: list[tuple[str, str, object]] = []
    for scope, scoped_list in client.aggregated_list(
        project=settings.GCP_PROJECT_ID
    ):
        zone = scope.split("/")[-1]
        for instance in scoped_list.instances:
            if instance.status != "RUNNING":
                logger.info("Leaving %s in %s unchanged", instance.name, instance.status)
                continue
            logger.info("Stopping %s in %s", instance.name, zone)
            operation = client.stop(
                project=settings.GCP_PROJECT_ID,
                zone=zone,
                instance=instance.name,
            )
            pending.append((zone, instance.name, operation))
    for zone, name, operation in pending:
        operation.result(timeout=INSTANCE_OPERATION_TIMEOUT_SECONDS)
        logger.info("Stopped %s in %s", name, zone)
    return [name for _, name, _ in pending]


def build_ssh_firewall_rules(
    settings: TestEnvironmentSettings,
) -> tuple[compute_v1.Firewall, compute_v1.Firewall]:
    """Allow IAP SSH and explicitly deny SSH from every other IPv4 source."""
    logger.info("Building IAP-only SSH firewall rules")
    network = (
        f"projects/{settings.GCP_PROJECT_ID}/global/networks/"
        f"{settings.GCP_NETWORK}"
    )
    allow_rule = compute_v1.Firewall(
        name=settings.GCP_SSH_FIREWALL_RULE,
        description="Allow IAP TCP forwarding to test VMs over SSH.",
        network=network,
        direction="INGRESS",
        priority=900,
        source_ranges=[settings.GCP_IAP_SOURCE_RANGE],
        target_tags=[settings.GCP_SSH_NETWORK_TAG],
        allowed=[compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])],
    )
    deny_rule = compute_v1.Firewall(
        name=f"{settings.GCP_SSH_FIREWALL_RULE}-deny",
        description="Deny SSH to test VMs from all non-IAP IPv4 sources.",
        network=network,
        direction="INGRESS",
        priority=1000,
        source_ranges=[str(ipaddress.IPv4Network((0, 0)))],
        target_tags=[settings.GCP_SSH_NETWORK_TAG],
        denied=[compute_v1.Denied(I_p_protocol="tcp", ports=["22"])],
    )
    return allow_rule, deny_rule


def _ensure_ssh_firewall_rule(
    settings: TestEnvironmentSettings,
    firewalls_client: compute_v1.FirewallsClient,
) -> None:
    logger.info("Checking scoped IAP SSH firewall rules")
    for desired in build_ssh_firewall_rules(settings):
        logger.info("Checking firewall rule %s", desired.name)
        try:
            existing = firewalls_client.get(
                project=settings.GCP_PROJECT_ID,
                firewall=desired.name,
            )
        except NotFound:
            logger.info("Creating missing firewall rule %s", desired.name)
            try:
                logger.info(
                    "Waiting for firewall rule operation %s to complete",
                    desired.name,
                )
                operation = firewalls_client.insert(
                    project=settings.GCP_PROJECT_ID,
                    firewall_resource=desired,
                )
                operation.result(timeout=INSTANCE_OPERATION_TIMEOUT_SECONDS)
                logger.info("Firewall rule %s created", desired.name)
                continue
            except AlreadyExists:
                existing = firewalls_client.get(
                    project=settings.GCP_PROJECT_ID,
                    firewall=desired.name,
                )
        mismatches = _firewall_rule_mismatches(existing, desired)
        if mismatches:
            logger.error(
                "Firewall rule %s differs in: %s",
                desired.name,
                ", ".join(mismatches),
            )
            raise RuntimeError(
                f"GCP firewall rule {desired.name!r} already exists with "
                "settings different from the approved SSH rule "
                f"(mismatched fields: {', '.join(mismatches)})"
            )
        logger.info("Firewall rule %s matches the required settings", desired.name)


def _firewall_rule_matches(
    existing: compute_v1.Firewall,
    desired: compute_v1.Firewall,
) -> bool:
    return not _firewall_rule_mismatches(existing, desired)


def _firewall_rule_mismatches(
    existing: compute_v1.Firewall,
    desired: compute_v1.Firewall,
) -> list[str]:
    logger.info("Comparing existing firewall rule with expected settings")
    mismatches: list[str] = []
    if not (existing.network or "").endswith(desired.network):
        mismatches.append("network")
    if existing.direction != desired.direction:
        mismatches.append("direction")
    if existing.priority != desired.priority:
        mismatches.append("priority")
    if existing.disabled:
        mismatches.append("disabled")
    if list(existing.source_ranges) != list(desired.source_ranges):
        mismatches.append("source_ranges")
    if existing.source_tags:
        mismatches.append("source_tags")
    if existing.source_service_accounts:
        mismatches.append("source_service_accounts")
    if list(existing.target_tags) != list(desired.target_tags):
        mismatches.append("target_tags")
    if existing.target_service_accounts:
        mismatches.append("target_service_accounts")
    if existing.destination_ranges:
        mismatches.append("destination_ranges")
    if desired.allowed:
        if len(existing.allowed) != 1 or existing.denied:
            mismatches.append("allowed/denied rules")
    elif desired.denied:
        if len(existing.denied) != 1 or existing.allowed:
            mismatches.append("allowed/denied rules")
    else:
        mismatches.append("allowed/denied rules")
    if desired.allowed and len(existing.allowed) == 1 and not existing.denied:
        rule = existing.allowed[0]
        if rule.I_p_protocol != "tcp" or list(rule.ports) != ["22"]:
            mismatches.append("allowed protocol/ports")
    elif desired.denied and len(existing.denied) == 1 and not existing.allowed:
        rule = existing.denied[0]
        if rule.I_p_protocol != "tcp" or list(rule.ports) != ["22"]:
            mismatches.append("denied protocol/ports")
    return mismatches


def _region_for_zone(settings: TestEnvironmentSettings) -> str:
    return settings.GCP_ZONE.rsplit("-", maxsplit=1)[0]


def build_cloud_router(settings: TestEnvironmentSettings) -> compute_v1.Router:
    """Build a dedicated Cloud Router and public Cloud NAT for the test subnet."""
    logger.info("Building Cloud Router and NAT configuration")
    region = _region_for_zone(settings)
    network = (
        f"projects/{settings.GCP_PROJECT_ID}/global/networks/"
        f"{settings.GCP_NETWORK}"
    )
    subnet = (
        f"projects/{settings.GCP_PROJECT_ID}/regions/{region}/subnetworks/"
        f"{settings.GCP_SUBNETWORK}"
    )
    return compute_v1.Router(
        name=NAT_ROUTER_NAME,
        network=network,
        nats=[
            compute_v1.RouterNat(
                name=NAT_CONFIG_NAME,
                type_="PUBLIC",
                nat_ip_allocate_option="AUTO_ONLY",
                source_subnetwork_ip_ranges_to_nat="LIST_OF_SUBNETWORKS",
                enable_endpoint_independent_mapping=True,
                subnetworks=[
                    compute_v1.RouterNatSubnetworkToNat(
                        name=subnet,
                        source_ip_ranges_to_nat=["ALL_IP_RANGES"],
                    )
                ],
            )
        ],
    )


def _router_mismatches(
    existing: compute_v1.Router,
    desired: compute_v1.Router,
) -> list[str]:
    logger.info("Comparing Cloud Router and NAT settings")
    mismatches: list[str] = []
    if not (existing.network or "").endswith(desired.network):
        mismatches.append("network")
    if len(existing.nats) != 1:
        mismatches.append("NAT configuration count")
        return mismatches

    actual_nat = existing.nats[0]
    expected_nat = desired.nats[0]
    if actual_nat.name != expected_nat.name:
        mismatches.append("NAT name")
    if actual_nat.type_ != expected_nat.type_:
        mismatches.append("NAT type")
    if actual_nat.nat_ip_allocate_option != expected_nat.nat_ip_allocate_option:
        mismatches.append("NAT IP allocation mode")
    if (
        actual_nat.source_subnetwork_ip_ranges_to_nat
        != expected_nat.source_subnetwork_ip_ranges_to_nat
    ):
        mismatches.append("NAT subnet selection mode")
    if (
        actual_nat.enable_endpoint_independent_mapping
        != expected_nat.enable_endpoint_independent_mapping
    ):
        mismatches.append("NAT endpoint-independent mapping")
    if actual_nat.rules:
        mismatches.append("NAT rules")
    if len(actual_nat.subnetworks) != 1:
        mismatches.append("NAT subnet count")
    else:
        actual_subnet = actual_nat.subnetworks[0]
        expected_subnet = expected_nat.subnetworks[0]
        if not (actual_subnet.name or "").endswith(expected_subnet.name):
            mismatches.append("NAT subnet")
        if list(actual_subnet.source_ip_ranges_to_nat) != list(
            expected_subnet.source_ip_ranges_to_nat
        ):
            mismatches.append("NAT source ranges")
        if actual_subnet.secondary_ip_range_names:
            mismatches.append("NAT secondary IP ranges")
    if actual_nat.nat64_subnetworks or actual_nat.source_subnetwork_ip_ranges_to_nat64:
        mismatches.append("NAT64 configuration")
    if actual_nat.nat_ips:
        mismatches.append("manually assigned NAT IPs")
    return mismatches


def ensure_cloud_nat(
    settings: TestEnvironmentSettings,
    routers_client: compute_v1.RoutersClient,
) -> compute_v1.Router:
    """Create or verify Cloud NAT before provisioning VMs in the subnet."""
    logger.info(
        "Ensuring Cloud NAT for subnet %s in region %s",
        settings.GCP_SUBNETWORK,
        _region_for_zone(settings),
    )
    desired = build_cloud_router(settings)
    try:
        existing = routers_client.get(
            project=settings.GCP_PROJECT_ID,
            region=_region_for_zone(settings),
            router=NAT_ROUTER_NAME,
        )
    except NotFound:
        logger.info("Creating dedicated Cloud Router and NAT")
        try:
            operation = routers_client.insert(
                project=settings.GCP_PROJECT_ID,
                region=_region_for_zone(settings),
                router_resource=desired,
            )
            logger.info("Waiting for Cloud Router creation to complete")
            operation.result(timeout=INSTANCE_OPERATION_TIMEOUT_SECONDS)
        except AlreadyExists:
            logger.info(
                "Another provisioning run created Cloud Router %s",
                NAT_ROUTER_NAME,
            )
        existing = routers_client.get(
            project=settings.GCP_PROJECT_ID,
            region=_region_for_zone(settings),
            router=NAT_ROUTER_NAME,
        )

    mismatches = _router_mismatches(existing, desired)
    if mismatches:
        logger.error(
            "Cloud Router %s differs in: %s",
            NAT_ROUTER_NAME,
            ", ".join(mismatches),
        )
        raise RuntimeError(
            f"Cloud Router {NAT_ROUTER_NAME!r} exists with settings "
            "different from the required Cloud NAT configuration "
            f"(mismatched fields: {', '.join(mismatches)})"
        )
    logger.info("Cloud Router and NAT match the required settings")
    return existing


def build_eu_server(settings: TestEnvironmentSettings) -> compute_v1.Instance:
    """Build the Ubuntu VM resource for the approved test configuration."""
    return _build_test_server(settings, INSTANCE_NAME)


def build_ru_server(settings: TestEnvironmentSettings) -> compute_v1.Instance:
    """Build the Ubuntu VM resource for ru-server."""
    return _build_test_server(settings, RU_INSTANCE_NAME)


def _build_test_server(
    settings: TestEnvironmentSettings,
    instance_name: str,
) -> compute_v1.Instance:
    logger.info("Building %s VM resource", instance_name)
    region = _region_for_zone(settings)
    network_path = (
        f"projects/{settings.GCP_PROJECT_ID}/global/networks/"
        f"{settings.GCP_NETWORK}"
    )
    subnet_path = (
        f"projects/{settings.GCP_PROJECT_ID}/regions/{region}/subnetworks/"
        f"{settings.GCP_SUBNETWORK}"
    )
    return compute_v1.Instance(
        name=instance_name,
        machine_type=(
            f"zones/{settings.GCP_ZONE}/machineTypes/"
            f"{settings.GCP_MACHINE_TYPE}"
        ),
        disks=[
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=(
                        f"projects/{settings.GCP_IMAGE_PROJECT}/global/"
                        f"images/family/{settings.GCP_IMAGE_FAMILY}"
                    ),
                    disk_size_gb=10,
                ),
            )
        ],
        network_interfaces=[
            compute_v1.NetworkInterface(
                network=network_path,
                subnetwork=subnet_path,
            )
        ],
        metadata=compute_v1.Metadata(
            items=[
                compute_v1.Items(
                    key="enable-oslogin",
                    value="TRUE",
                )
            ]
        ),
        service_accounts=[],
        tags=compute_v1.Tags(items=[settings.GCP_SSH_NETWORK_TAG]),
    )


def _resolve_clients(
    instances_client: compute_v1.InstancesClient | None = None,
    firewalls_client: compute_v1.FirewallsClient | None = None,
    routers_client: compute_v1.RoutersClient | None = None,
) -> tuple[
    compute_v1.InstancesClient,
    compute_v1.FirewallsClient,
    compute_v1.RoutersClient,
]:
    """Return the supplied Compute clients, creating any that are missing."""
    if instances_client and firewalls_client and routers_client:
        return instances_client, firewalls_client, routers_client
    logger.info("Creating authenticated Compute Engine API clients")
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return (
        instances_client or compute_v1.InstancesClient(credentials=credentials),
        firewalls_client or compute_v1.FirewallsClient(credentials=credentials),
        routers_client or compute_v1.RoutersClient(credentials=credentials),
    )


def _lookup_instance(
    settings: TestEnvironmentSettings,
    instance_name: str,
    instances_client: compute_v1.InstancesClient,
) -> compute_v1.Instance | None:
    logger.info(
        "Looking up %s in project %s, zone %s",
        instance_name,
        settings.GCP_PROJECT_ID,
        settings.GCP_ZONE,
    )
    try:
        instance = instances_client.get(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance=instance_name,
        )
    except NotFound:
        logger.info("%s does not exist in the configured zone", instance_name)
        return None
    logger.info("Found existing VM %s", instance_name)
    return instance


def _get_or_create_instance(
    settings: TestEnvironmentSettings,
    desired_instance: compute_v1.Instance,
    instances_client: compute_v1.InstancesClient,
) -> compute_v1.Instance:
    instance_name = desired_instance.name
    instance = _lookup_instance(settings, instance_name, instances_client)
    if instance is not None:
        return instance

    logger.info("VM %s does not exist; creating it", instance_name)
    try:
        logger.info("Waiting for VM creation operation to complete")
        operation = instances_client.insert(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance_resource=desired_instance,
        )
        operation.result(timeout=INSTANCE_OPERATION_TIMEOUT_SECONDS)
        logger.info("VM creation operation completed for %s", instance_name)
    except AlreadyExists:
        logger.info(
            "Another provisioning run created %s; retrieving it",
            instance_name,
        )
    return instances_client.get(
        project=settings.GCP_PROJECT_ID,
        zone=settings.GCP_ZONE,
        instance=instance_name,
    )


def lookup_ru_server(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient | None = None,
) -> compute_v1.Instance | None:
    """Look up ru-server in the configured project and zone without creating it."""
    logger.info(
        "Looking up %s in project %s, zone %s",
        RU_INSTANCE_NAME,
        settings.GCP_PROJECT_ID,
        settings.GCP_ZONE,
    )
    if instances_client is None:
        instances_client, _, _ = _resolve_clients()

    instance = _lookup_instance(settings, RU_INSTANCE_NAME, instances_client)
    if instance is None:
        return None

    _validate_ru_server_network(instance, settings)
    logger.info("%s exists in the configured test network", RU_INSTANCE_NAME)
    return instance


def _validate_ru_server_network(
    instance: compute_v1.Instance,
    settings: TestEnvironmentSettings,
) -> None:
    expected_network = (
        f"projects/{settings.GCP_PROJECT_ID}/global/networks/"
        f"{settings.GCP_NETWORK}"
    )
    if not instance.network_interfaces or not any(
        (interface.network or "").endswith(expected_network)
        for interface in instance.network_interfaces
    ):
        raise RuntimeError(
            f"Existing {RU_INSTANCE_NAME} is not connected to the configured "
            f"test network {settings.GCP_NETWORK!r}; it was not modified"
        )


def _validate_instance_configuration(
    instance: compute_v1.Instance,
    settings: TestEnvironmentSettings,
    instance_name: str = INSTANCE_NAME,
) -> None:
    """Refuse to use a same-named VM that does not meet approved access settings."""
    logger.info("Validating existing %s configuration", instance_name)
    expected = _build_test_server(settings, instance_name)
    if not instance.machine_type.endswith(expected.machine_type):
        raise RuntimeError(
            f"Existing {instance_name} has a machine type different from the "
            "configured test VM; it was not modified"
        )
    if not instance.tags or settings.GCP_SSH_NETWORK_TAG not in instance.tags.items:
        raise RuntimeError(
            f"Existing {instance_name} lacks the dedicated SSH firewall tag; "
            "it was not modified"
        )
    if len(instance.network_interfaces) != 1:
        raise RuntimeError(
            f"Existing {instance_name} must have exactly one network interface; "
            "it was not modified"
        )
    actual_interface = instance.network_interfaces[0]
    if (
        not (actual_interface.network or "").endswith(
            f"/global/networks/{settings.GCP_NETWORK}"
        )
        or not (actual_interface.subnetwork or "").endswith(
            f"/subnetworks/{settings.GCP_SUBNETWORK}"
        )
        or actual_interface.access_configs
        or actual_interface.ipv6_access_configs
        or actual_interface.ipv6_access_type == "EXTERNAL"
    ):
        raise RuntimeError(
            f"Existing {instance_name} has a network configuration different "
            "from the approved VM without external IPs; it was not modified"
        )
    if not instance.metadata or not any(
        item.key == "enable-oslogin" and item.value == "TRUE"
        for item in instance.metadata.items
    ):
        raise RuntimeError(
            f"Existing {instance_name} does not have OS Login enabled; "
            "it was not modified"
        )
    if not instance.disks or not any(disk.boot for disk in instance.disks):
        raise RuntimeError(
            f"Existing {instance_name} has no boot disk; it was not modified"
        )
    logger.info("%s configuration matches the approved settings", instance_name)


def _wait_for_running(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient,
    instance_name: str = INSTANCE_NAME,
) -> compute_v1.Instance:
    logger.info("Waiting for %s to reach RUNNING", instance_name)
    deadline = time.monotonic() + INSTANCE_READY_TIMEOUT_SECONDS
    last_status: str | None = None
    while True:
        instance = instances_client.get(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance=instance_name,
        )
        if instance.status == "RUNNING":
            logger.info("%s is RUNNING", instance_name)
            return instance
        if instance.status != last_status:
            logger.info("%s status: %s", instance_name, instance.status)
            last_status = instance.status
        if instance.status in {"TERMINATED", "SUSPENDED"}:
            raise RuntimeError(
                f"{instance_name} entered unexpected state {instance.status}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"{instance_name} did not reach RUNNING within "
                f"{INSTANCE_READY_TIMEOUT_SECONDS} seconds"
            )
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _wait_for_ssh(
    settings: TestEnvironmentSettings,
    instance_name: str = INSTANCE_NAME,
) -> None:
    logger.info("Waiting for OS Login SSH readiness through IAP")
    deadline = time.monotonic() + SSH_READY_TIMEOUT_SECONDS
    last_attempt = "no SSH attempt completed"
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Could not authenticate to {instance_name} over SSH within "
                f"{SSH_READY_TIMEOUT_SECONDS} seconds. Last attempt: "
                f"{last_attempt}."
            )
        try:
            attempt += 1
            result = subprocess.run(
                [
                    "gcloud",
                    "compute",
                    "ssh",
                    instance_name,
                    f"--project={settings.GCP_PROJECT_ID}",
                    f"--zone={settings.GCP_ZONE}",
                    "--tunnel-through-iap",
                    "--command=true",
                    "--quiet",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=min(SSH_COMMAND_TIMEOUT_SECONDS, remaining),
            )
            if result.returncode == 0:
                logger.info("IAP SSH readiness check succeeded")
                return
            last_attempt = f"gcloud exited with status {result.returncode}"
            logger.info(
                "IAP SSH attempt %d is not ready yet (gcloud exit code %d)",
                attempt,
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            last_attempt = "gcloud SSH command timed out"
            logger.info("IAP SSH attempt %d timed out; will retry", attempt)

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def ensure_eu_server(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient | None = None,
    firewalls_client: compute_v1.FirewallsClient | None = None,
    routers_client: compute_v1.RoutersClient | None = None,
) -> compute_v1.Instance:
    """Create or reuse eu-server, then wait for authenticated SSH readiness."""
    return _ensure_server(
        settings,
        build_eu_server(settings),
        instances_client,
        firewalls_client,
        routers_client,
    )


def ensure_ru_server(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient | None = None,
    firewalls_client: compute_v1.FirewallsClient | None = None,
    routers_client: compute_v1.RoutersClient | None = None,
) -> compute_v1.Instance:
    """Create or reuse ru-server, then wait for authenticated SSH readiness."""
    return _ensure_server(
        settings,
        build_ru_server(settings),
        instances_client,
        firewalls_client,
        routers_client,
    )


def _ensure_server(
    settings: TestEnvironmentSettings,
    desired_instance: compute_v1.Instance,
    instances_client: compute_v1.InstancesClient | None,
    firewalls_client: compute_v1.FirewallsClient | None,
    routers_client: compute_v1.RoutersClient | None,
) -> compute_v1.Instance:
    instance_name = desired_instance.name
    logger.info("Starting %s provisioning", instance_name)
    _require_gcloud_cli()
    instances_client, firewalls_client, routers_client = _resolve_clients(
        instances_client, firewalls_client, routers_client
    )

    ensure_cloud_nat(settings, routers_client)
    _ensure_ssh_firewall_rule(settings, firewalls_client)
    instance = _get_or_create_instance(settings, desired_instance, instances_client)
    _validate_instance_configuration(
        instance, settings, instance_name=instance_name
    )
    instance = _wait_for_running(
        settings, instances_client, instance_name=instance_name
    )
    _wait_for_ssh(settings, instance_name=instance_name)
    logger.info("%s is provisioned, running, and SSH-ready", instance_name)
    return instance
