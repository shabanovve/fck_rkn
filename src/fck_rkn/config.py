"""Configuration loading and validation for the test environment."""

from __future__ import annotations

import ipaddress
import logging
import re
from pathlib import Path

from dotenv.parser import parse_stream
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPOSITORY_ROOT / "config" / "test.env"
GCP_PROJECT_ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
TEST_PROJECT_ID = "fck-rkn-test"
GCP_ZONE_PATTERN = re.compile(r"[a-z]+-[a-z0-9]+[0-9]-[a-z]\Z")

logger = logging.getLogger(__name__)


class DotenvSyntaxError(ValueError):
    """A dotenv syntax error reported without exposing the offending line."""

    def __init__(self, path: Path, line: int) -> None:
        self.path = path
        self.line = line
        super().__init__(f"Invalid .env syntax in {path.name} at line {line}")


class TestEnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=CONFIG_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    GCP_PROJECT_ID: str = Field(min_length=6, max_length=30)
    GCP_ZONE: str = "europe-north1-a"
    GCP_MACHINE_TYPE: str = "e2-micro"
    GCP_IMAGE_PROJECT: str = "ubuntu-os-cloud"
    GCP_IMAGE_FAMILY: str = "ubuntu-2404-lts-amd64"
    GCP_NETWORK: str = "default"
    GCP_SUBNETWORK: str = "default"
    GCP_IAP_SOURCE_RANGE: str
    GCP_SSH_NETWORK_TAG: str = "fck-rkn-test-eu-server-ssh"
    GCP_SSH_FIREWALL_RULE: str = "fck-rkn-test-eu-server-ssh"

    @field_validator("GCP_PROJECT_ID")
    @classmethod
    def validate_gcp_project_id(cls, value: str) -> str:
        logger.info("Validating configured GCP project ID")
        if not GCP_PROJECT_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "must be 6-30 lowercase letters, digits, or hyphens, "
                "start with a letter, and end with a letter or digit"
            )
        if value != TEST_PROJECT_ID:
            raise ValueError(
                f"must target the isolated test project {TEST_PROJECT_ID!r}"
            )
        return value

    @field_validator("GCP_ZONE")
    @classmethod
    def validate_gcp_zone(cls, value: str) -> str:
        logger.info("Validating configured GCP zone")
        if not GCP_ZONE_PATTERN.fullmatch(value):
            raise ValueError("must be a GCP zone such as europe-north1-a")
        return value

    @field_validator("GCP_IAP_SOURCE_RANGE")
    @classmethod
    def validate_iap_source_range(cls, value: str) -> str:
        logger.info("Validating configured IAP source range")
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as error:
            raise ValueError(
                "must be the IPv4 CIDR range currently documented for "
                "Google Cloud IAP TCP forwarding"
            ) from error
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError("must be an IPv4 CIDR range")
        if network.prefixlen != 20 or not network.network_address.is_global:
            raise ValueError(
                "must be the public IPv4 /20 range documented for "
                "Google Cloud IAP TCP forwarding"
            )
        return value


def load_config(path: Path | None = None) -> TestEnvironmentSettings:
    """Load and validate the test configuration before cloud API operations."""
    config_path = CONFIG_FILE if path is None else path
    logger.info("Loading test configuration from %s", config_path)
    try:
        with config_path.open(encoding="utf-8") as config_file:
            for binding in parse_stream(config_file):
                if binding.error:
                    raise DotenvSyntaxError(config_path, binding.original.line)
    except UnicodeError as error:
        raise ValueError(
            f"{config_path.name} must be valid UTF-8; its contents are not shown"
        ) from error
    settings = TestEnvironmentSettings(_env_file=config_path)
    logger.info(
        "Configuration validated for project %s in zone %s",
        settings.GCP_PROJECT_ID,
        settings.GCP_ZONE,
    )
    return settings
