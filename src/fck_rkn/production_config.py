"""Production-only configuration and safe local template initialization."""

from __future__ import annotations

import ipaddress
import os
import re
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROD_CONFIG_FILE = REPOSITORY_ROOT / "config" / "prod.env"
PROD_TEMPLATE_FILE = PROD_CONFIG_FILE.with_name("prod.env.example")
HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
PLACEHOLDER_MARKERS = ("SET_TO_", "TODO", "<", ">")


class ProductionConfigError(ValueError):
    """A production configuration error without the offending value."""


class ProductionDotenvSyntaxError(ProductionConfigError):
    """A dotenv syntax error reported without exposing its line contents."""

    def __init__(self, path: Path, line: int) -> None:
        self.path = path
        self.line = line
        super().__init__(
            f"Invalid production dotenv syntax in {path.name} at line {line}"
        )


class EuProductionSettings(BaseSettings):
    """Validated configuration required by the EU-only production stage."""

    model_config = SettingsConfigDict(
        env_file=PROD_CONFIG_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: object,
        env_settings: object,
        dotenv_settings: object,
        file_secret_settings: object,
    ) -> tuple[object, ...]:
        return init_settings, dotenv_settings

    EU_SERVER_HOST: str = Field(min_length=1)
    EU_SERVER_ADDRESS: str = Field(min_length=1)
    RU_SERVER_ADDRESS: str = Field(min_length=1)
    SSH_USER: str = Field(min_length=1)
    SSH_KEY_PATH: Path
    SSH_PORT: int = Field(default=22, ge=1, le=65535)
    EU_METADATA_ADDRESS: str = Field(min_length=1)
    EU_DNS_ADDRESSES: list[str]
    EU_DHCP_SERVER_ADDRESS: str = Field(min_length=1)
    EU_NTP_ADDRESSES: list[str]
    EU_LOCAL_NETWORKS: list[str]

    @field_validator(
        "EU_SERVER_HOST",
        "EU_SERVER_ADDRESS",
        "RU_SERVER_ADDRESS",
        "SSH_USER",
        "EU_METADATA_ADDRESS",
        "EU_DHCP_SERVER_ADDRESS",
        mode="before",
    )
    @classmethod
    def reject_placeholders(cls, value: object) -> object:
        if isinstance(value, str) and any(
            marker in value.upper() for marker in PLACEHOLDER_MARKERS
        ):
            raise ValueError("must be replaced with an operator-supplied value")
        return value

    @field_validator("EU_SERVER_HOST")
    @classmethod
    def validate_server_host(cls, value: str) -> str:
        candidate = value.strip()
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            if (
                len(candidate) > 253
                or not candidate
                or any(
                    not HOST_LABEL_PATTERN.fullmatch(label)
                    for label in candidate.rstrip(".").split(".")
                )
            ):
                raise ValueError("must be an IP address or DNS hostname")
            return candidate.rstrip(".")

    @field_validator(
        "EU_SERVER_ADDRESS",
        "RU_SERVER_ADDRESS",
        "EU_METADATA_ADDRESS",
        "EU_DHCP_SERVER_ADDRESS",
    )
    @classmethod
    def validate_ip_address(cls, value: str) -> str:
        try:
            return ipaddress.ip_address(value.strip()).compressed
        except ValueError as error:
            raise ValueError("must be an IPv4 or IPv6 address") from error

    @field_validator("SSH_USER")
    @classmethod
    def validate_ssh_user(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", value):
            raise ValueError("must be a valid SSH account name")
        return value

    @field_validator("SSH_KEY_PATH")
    @classmethod
    def validate_ssh_key_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("must be an absolute path on the controller")
        if any(marker in str(value).upper() for marker in PLACEHOLDER_MARKERS):
            raise ValueError("must be replaced with an operator-supplied path")
        return value

    @field_validator("EU_DNS_ADDRESSES", "EU_NTP_ADDRESSES")
    @classmethod
    def validate_address_lists(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("must contain at least one address")
        result = []
        for value in values:
            try:
                result.append(ipaddress.ip_address(value.strip()).compressed)
            except ValueError as error:
                raise ValueError(
                    "must contain only IPv4 or IPv6 addresses"
                ) from error
        return result

    @field_validator("EU_LOCAL_NETWORKS")
    @classmethod
    def validate_local_networks(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("must contain at least one CIDR network")
        try:
            return [
                ipaddress.ip_network(value.strip(), strict=True).with_prefixlen
                for value in values
            ]
        except ValueError as error:
            raise ValueError(
                "must contain only valid IPv4 or IPv6 CIDR networks"
            ) from error


def ensure_prod_config(
    config_path: Path = PROD_CONFIG_FILE,
    template_path: Path = PROD_TEMPLATE_FILE,
) -> bool:
    """Create a protected production config from its template if it is absent.

    The destination is published atomically without replacing a file created
    concurrently by another process. Returns True only when this call creates
    the file. Existing operator settings are never modified.
    """
    if config_path.exists():
        return False

    template = template_path.read_text(encoding="utf-8")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{config_path.name}."
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(template)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, config_path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def load_eu_production_settings(
    path: Path = PROD_CONFIG_FILE,
) -> EuProductionSettings:
    """Load EU production settings after syntax checking without leaking values."""
    try:
        with path.open(encoding="utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.error:
                    raise ProductionDotenvSyntaxError(path, binding.original.line)
    except OSError as error:
        raise ProductionConfigError(
            f"Could not read production configuration {path.name}"
        ) from error
    except UnicodeError as error:
        raise ProductionConfigError(
            f"{path.name} must be valid UTF-8; its contents are not shown"
        ) from error

    try:
        return EuProductionSettings(_env_file=path)
    except SettingsError as error:
        raise ProductionConfigError(
            f"Could not read production configuration {path.name}"
        ) from error
    except ValidationError as error:
        fields = sorted(
            {
                ".".join(str(part) for part in detail["loc"])
                for detail in error.errors(include_input=False, include_context=False)
            }
        )
        raise ProductionConfigError(
            f"Invalid production configuration: {', '.join(fields)}"
        ) from error
