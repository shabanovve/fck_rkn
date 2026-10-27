"""Production-only configuration and safe local template initialization."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROD_CONFIG_FILE = REPOSITORY_ROOT / "config" / "prod.env"
PROD_TEMPLATE_FILE = PROD_CONFIG_FILE.with_name("prod.env.example")
PLACEHOLDER_MARKERS = ("SET_TO_", "TODO", "<", ">")
JSON_LIST_FIELDS = {
    "EU_DNS_ADDRESSES": "a JSON array of IPv4/IPv6 addresses",
    "EU_NTP_ADDRESSES": "a JSON array of IPv4/IPv6 addresses",
    "EU_LOCAL_NETWORKS": "a JSON array of CIDR networks",
    "RU_DNS_ADDRESSES": "a JSON array of IPv4/IPv6 addresses",
    "RU_NTP_ADDRESSES": "a JSON array of IPv4/IPv6 addresses",
    "RU_LOCAL_NETWORKS": "a JSON array of CIDR networks",
    "RU_SERVER_TO_GITHUB_ADDRESSES": "a JSON array of CIDR networks",
}
OPTIONAL_RU_FIELDS = (
    "RU_SERVER_ADDRESS",
    "RU_SSH_USER",
    "RU_SSH_KEY_PATH",
    "RU_SSH_PORT",
    "RU_DNS_ADDRESSES",
    "RU_DHCP_SERVER_ADDRESS",
    "RU_NTP_ADDRESSES",
    "RU_LOCAL_NETWORKS",
    "RU_SERVER_TO_GITHUB_ADDRESSES",
)


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

    EU_SERVER_ADDRESS: str = Field(min_length=1)
    RU_SERVER_ADDRESS: str | None = None
    SSH_USER: str = Field(min_length=1)
    SSH_KEY_PATH: Path
    SSH_PORT: int = Field(default=22, ge=1, le=65535)
    RU_SSH_USER: str | None = None
    RU_SSH_KEY_PATH: Path | None = None
    RU_SSH_PORT: int | None = None
    EU_DNS_ADDRESSES: list[str]
    EU_DHCP_SERVER_ADDRESS: str = Field(min_length=1)
    EU_NTP_ADDRESSES: list[str]
    EU_LOCAL_NETWORKS: list[str]
    RU_DNS_ADDRESSES: list[str] | None = None
    RU_DHCP_SERVER_ADDRESS: str | None = None
    RU_NTP_ADDRESSES: list[str] | None = None
    RU_LOCAL_NETWORKS: list[str] | None = None
    RU_SERVER_TO_GITHUB_ADDRESSES: list[str] | None = None

    @field_validator(
        "EU_SERVER_ADDRESS",
        "RU_SERVER_ADDRESS",
        "SSH_USER",
        "RU_SSH_USER",
        "EU_DHCP_SERVER_ADDRESS",
            "RU_DHCP_SERVER_ADDRESS",
        mode="before",
    )
    @classmethod
    def reject_placeholders(cls, value: object) -> object:
        if isinstance(value, str) and any(
            marker in value.upper() for marker in PLACEHOLDER_MARKERS
        ):
            raise ValueError("must be replaced with an operator-supplied value")
        return value

    @field_validator(
        "EU_SERVER_ADDRESS",
        "RU_SERVER_ADDRESS",
        "EU_DHCP_SERVER_ADDRESS",
            "RU_DHCP_SERVER_ADDRESS",
    )
    @classmethod
    def validate_ip_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return ipaddress.ip_address(value.strip()).compressed
        except ValueError as error:
            raise ValueError("must be an IPv4 or IPv6 address") from error

    @field_validator("SSH_USER", "RU_SSH_USER")
    @classmethod
    def validate_ssh_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", value):
            raise ValueError("must be a valid SSH account name")
        return value

    @field_validator("SSH_KEY_PATH", "RU_SSH_KEY_PATH")
    @classmethod
    def validate_ssh_key_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("must be an absolute path on the controller")
        if any(marker in str(value).upper() for marker in PLACEHOLDER_MARKERS):
            raise ValueError("must be replaced with an operator-supplied path")
        return value

    @field_validator(
        "EU_DNS_ADDRESSES",
        "EU_NTP_ADDRESSES",
        "RU_DNS_ADDRESSES",
        "RU_NTP_ADDRESSES",
    )
    @classmethod
    def validate_address_lists(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is None:
            return None
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

    @field_validator("EU_LOCAL_NETWORKS", "RU_LOCAL_NETWORKS", "RU_SERVER_TO_GITHUB_ADDRESSES")
    @classmethod
    def validate_local_networks(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is None:
            return None
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
        values = dotenv_values(path, encoding="utf-8", interpolate=False)
    except OSError as error:
        raise ProductionConfigError(
            f"Could not read production configuration {path.name}"
        ) from error
    except UnicodeError as error:
        raise ProductionConfigError(
            f"{path.name} must be valid UTF-8; its contents are not shown"
        ) from error

    issues: dict[str, str] = {}
    for field in OPTIONAL_RU_FIELDS:
        if values.get(field) == "":
            values[field] = None
    for field, expected in JSON_LIST_FIELDS.items():
        raw_value = values.get(field)
        if raw_value is None or raw_value == "":
            continue
        if any(marker in raw_value.upper() for marker in PLACEHOLDER_MARKERS):
            issues[field] = "contains a placeholder that must be replaced"
            values.pop(field)
            continue
        try:
            parsed_value = json.loads(raw_value)
        except json.JSONDecodeError:
            issues[field] = f"must be {expected}"
            values.pop(field)
        else:
            if not isinstance(parsed_value, list):
                issues[field] = f"must be {expected}"
                values.pop(field)
            else:
                values[field] = parsed_value

    try:
        settings = EuProductionSettings(_env_file=None, **values)
    except SettingsError as error:
        raise ProductionConfigError(
            f"Could not parse production configuration {path.name}"
        ) from error
    except ValidationError as error:
        validation_errors = error.errors(
            include_input=False, include_context=False
        )
        for detail in validation_errors:
            field = str(detail["loc"][0])
            if field in issues:
                continue
            reason = str(detail["msg"])
            if reason.startswith("Value error, "):
                reason = reason.removeprefix("Value error, ")
            issues.setdefault(field, reason)

    if issues:
        details = "\n".join(
            f"- {field}: {reason}" for field, reason in sorted(issues.items())
        )
        raise ProductionConfigError(
            f"Invalid production configuration in {path.name}:\n{details}"
        )
    return settings
