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
        description="Allow IAP TCP forwarding to eu-server over SSH.",
        network=network,
        direction="INGRESS",
        priority=900,
        source_ranges=[settings.GCP_IAP_SOURCE_RANGE],
        target_tags=[settings.GCP_SSH_NETWORK_TAG],
        allowed=[compute_v1.Allowed(I_p_protocol="tcp", ports=["22"])],
    )
    deny_rule = compute_v1.Firewall(
        name=f"{settings.GCP_SSH_FIREWALL_RULE}-deny",
        description="Deny SSH to eu-server from all non-IAP IPv4 sources.",
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


def build_eu_server(settings: TestEnvironmentSettings) -> compute_v1.Instance:
    """Build the Ubuntu VM resource for the approved test configuration."""
    logger.info("Building eu-server VM resource")
    region = settings.GCP_ZONE.rsplit("-", maxsplit=1)[0]
    network_path = (
        f"projects/{settings.GCP_PROJECT_ID}/global/networks/"
        f"{settings.GCP_NETWORK}"
    )
    subnet_path = (
        f"projects/{settings.GCP_PROJECT_ID}/regions/{region}/subnetworks/"
        f"{settings.GCP_SUBNETWORK}"
    )
    return compute_v1.Instance(
        name=INSTANCE_NAME,
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


def _get_or_create_eu_server(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient,
) -> compute_v1.Instance:
    logger.info(
        "Looking up %s in project %s, zone %s",
        INSTANCE_NAME,
        settings.GCP_PROJECT_ID,
        settings.GCP_ZONE,
    )
    try:
        instance = instances_client.get(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance=INSTANCE_NAME,
        )
        logger.info("Found existing VM %s", INSTANCE_NAME)
        return instance
    except NotFound:
        logger.info("VM %s does not exist; creating it", INSTANCE_NAME)
        instance = build_eu_server(settings)
        try:
            logger.info("Waiting for VM creation operation to complete")
            operation = instances_client.insert(
                project=settings.GCP_PROJECT_ID,
                zone=settings.GCP_ZONE,
                instance_resource=instance,
            )
            operation.result(timeout=INSTANCE_OPERATION_TIMEOUT_SECONDS)
            logger.info("VM creation operation completed for %s", INSTANCE_NAME)
        except AlreadyExists:
            logger.info(
                "Another provisioning run created %s; retrieving it",
                INSTANCE_NAME,
            )
        return instances_client.get(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance=INSTANCE_NAME,
        )


def _validate_instance_configuration(
    instance: compute_v1.Instance,
    settings: TestEnvironmentSettings,
) -> None:
    """Refuse to use a same-named VM that does not meet approved access settings."""
    logger.info("Validating existing %s configuration", INSTANCE_NAME)
    expected = build_eu_server(settings)
    if not instance.machine_type.endswith(expected.machine_type):
        raise RuntimeError(
            f"Existing {INSTANCE_NAME} has a machine type different from the "
            "configured test VM; it was not modified"
        )
    if not instance.tags or settings.GCP_SSH_NETWORK_TAG not in instance.tags.items:
        raise RuntimeError(
            f"Existing {INSTANCE_NAME} lacks the dedicated SSH firewall tag; "
            "it was not modified"
        )
    if len(instance.network_interfaces) != 1:
        raise RuntimeError(
            f"Existing {INSTANCE_NAME} must have exactly one network interface; "
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
            f"Existing {INSTANCE_NAME} has a network configuration different "
            "from the approved VM without external IPs; it was not modified"
        )
    if not instance.metadata or not any(
        item.key == "enable-oslogin" and item.value == "TRUE"
        for item in instance.metadata.items
    ):
        raise RuntimeError(
            f"Existing {INSTANCE_NAME} does not have OS Login enabled; "
            "it was not modified"
        )
    if not instance.disks or not any(disk.boot for disk in instance.disks):
        raise RuntimeError(
            f"Existing {INSTANCE_NAME} has no boot disk; it was not modified"
        )
    logger.info("%s configuration matches the approved settings", INSTANCE_NAME)


def _wait_for_running(
    settings: TestEnvironmentSettings,
    instances_client: compute_v1.InstancesClient,
) -> compute_v1.Instance:
    logger.info("Waiting for %s to reach RUNNING", INSTANCE_NAME)
    deadline = time.monotonic() + INSTANCE_READY_TIMEOUT_SECONDS
    last_status: str | None = None
    while True:
        instance = instances_client.get(
            project=settings.GCP_PROJECT_ID,
            zone=settings.GCP_ZONE,
            instance=INSTANCE_NAME,
        )
        if instance.status == "RUNNING":
            logger.info("%s is RUNNING", INSTANCE_NAME)
            return instance
        if instance.status != last_status:
            logger.info("%s status: %s", INSTANCE_NAME, instance.status)
            last_status = instance.status
        if instance.status in {"TERMINATED", "SUSPENDED"}:
            raise RuntimeError(
                f"{INSTANCE_NAME} entered unexpected state {instance.status}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"{INSTANCE_NAME} did not reach RUNNING within "
                f"{INSTANCE_READY_TIMEOUT_SECONDS} seconds"
            )
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _wait_for_ssh(settings: TestEnvironmentSettings) -> None:
    logger.info("Waiting for OS Login SSH readiness through IAP")
    deadline = time.monotonic() + SSH_READY_TIMEOUT_SECONDS
    last_attempt = "no SSH attempt completed"
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Could not authenticate to {INSTANCE_NAME} over SSH within "
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
                    INSTANCE_NAME,
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
) -> compute_v1.Instance:
    """Create or reuse eu-server, then wait for authenticated SSH readiness."""
    logger.info("Starting eu-server provisioning")
    _require_gcloud_cli()
    if instances_client is None or firewalls_client is None:
        logger.info("Creating authenticated Compute Engine API clients")
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        instances_client = instances_client or compute_v1.InstancesClient(
            credentials=credentials
        )
        firewalls_client = firewalls_client or compute_v1.FirewallsClient(
            credentials=credentials
        )

    _ensure_ssh_firewall_rule(settings, firewalls_client)
    instance = _get_or_create_eu_server(settings, instances_client)
    _validate_instance_configuration(instance, settings)
    instance = _wait_for_running(settings, instances_client)
    _wait_for_ssh(settings)
    logger.info("eu-server is provisioned, running, and SSH-ready")
    return instance
