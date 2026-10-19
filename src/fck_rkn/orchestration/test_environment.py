"""Command-line entry point for the fck-rkn-test environment orchestrator."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import compute_v1
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import (
    create_outline_user,
    deploy_outline,
    deploy_squid,
    run_youtube_ping,
)
from fck_rkn.agents.eu_server_test import (
    check_outline_health,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.agents.ru_server import deploy_outline_client, deploy_squid_forward
from fck_rkn.agents.ru_server_test import check_outline_client, check_squid_forward
from fck_rkn.config import DotenvSyntaxError, TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import (
    ensure_eu_server,
    ensure_ru_server,
    stop_test_project_vms,
    validate_project_access,
)


logger = logging.getLogger(__name__)
RU_PROBE_OUTLINE_USER = "ru-server-feasibility-probe"


def _shutdown_vms(settings: TestEnvironmentSettings) -> int:
    try:
        stopped = stop_test_project_vms(settings)
    except GoogleAuthError:
        logger.error("GCP authentication failed during VM shutdown")
        print("GCP authentication failed during VM shutdown.", file=sys.stderr)
        return 3
    except GoogleAPICallError as error:
        logger.error("GCP VM shutdown failed")
        print("GCP VM shutdown failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 14
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.error("VM shutdown failed: %s", error)
        print(f"VM shutdown failed: {error}", file=sys.stderr)
        return 14
    print(f"Stopped {len(stopped)} running VM(s) in {settings.GCP_PROJECT_ID}.")
    return 0


def _provision_server(
    name: str,
    ensure_server: Callable[[TestEnvironmentSettings], compute_v1.Instance],
    settings: TestEnvironmentSettings,
    failure_code: int,
    auth_code: int,
) -> tuple[compute_v1.Instance | None, int]:
    """Provision a VM; on failure report it and return (None, exit code)."""
    try:
        return ensure_server(settings), 0
    except GoogleAPICallError as error:
        logger.error("GCP %s provisioning failed", name)
        print(f"GCP {name} provisioning failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return None, failure_code
    except GoogleAuthError:
        logger.error("GCP authentication failed during %s provisioning", name)
        print(
            f"GCP authentication failed during {name} provisioning. "
            "Verify the active gcloud account and ADC credentials.",
            file=sys.stderr,
        )
        return None, auth_code
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.error("%s provisioning failed: %s", name, error)
        print(f"{name} provisioning failed: {error}", file=sys.stderr)
        return None, failure_code


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logger.info("Starting test environment orchestrator")
    try:
        settings: TestEnvironmentSettings = load_config()
    except DotenvSyntaxError as error:
        logger.error("Test configuration has invalid dotenv syntax")
        print("Configuration file syntax error.", file=sys.stderr)
        print(f"  File: {error.path}", file=sys.stderr)
        print(f"  Line: {error.line}", file=sys.stderr)
        print(
            "  Check that each non-comment line uses KEY=value syntax. "
            "The line contents are intentionally not shown.",
            file=sys.stderr,
        )
        return 2
    except OSError as error:
        logger.error("Could not read the test configuration file")
        print("Could not read the test configuration file.", file=sys.stderr)
        print(f"  File: {error.filename or 'config/test.env'}", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        return 2
    except (SettingsError, ValidationError) as error:
        logger.error("Test configuration is missing or invalid")
        print(
            "Configuration error: test settings are missing or invalid.",
            file=sys.stderr,
        )
        if isinstance(error, ValidationError):
            details = error.errors(include_input=False)
            missing = sorted(
                {
                    str(detail["loc"][0])
                    for detail in details
                    if detail["type"] == "missing"
                }
            )
            if missing:
                message = f"  Missing required setting(s): {', '.join(missing)}."
                if "GCP_IAP_SOURCE_RANGE" in missing:
                    message += (
                        " Set it in config/test.env to the current CIDR from "
                        "Google's official IAP TCP forwarding documentation."
                    )
                print(message, file=sys.stderr)
            else:
                print(details, file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 2

    try:
        validate_project_access(settings)
    except GoogleAuthError:
        logger.error("GCP authentication failed during project access check")
        print(
            "GCP authentication failed. Configure Application Default "
            "Credentials with `gcloud auth application-default login`.",
            file=sys.stderr,
        )
        return 3
    except GoogleAPICallError as error:
        logger.error("GCP project access check failed")
        print("GCP project access check failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 4
    except ValueError as error:
        logger.error("GCP project validation failed")
        print(f"GCP project validation failed: {error}", file=sys.stderr)
        return 4

    if sys.argv[1:2] == ["shutdown-vms"]:
        return _shutdown_vms(settings)

    instance, exit_code = _provision_server(
        "eu-server", ensure_eu_server, settings, failure_code=5, auth_code=3
    )
    if instance is None:
        return exit_code

    print(
        f"GCP access is valid; {instance.name} is running and SSH-ready "
        f"in {settings.GCP_ZONE}."
    )
    logger.info("Invoking eu-server-agent ping check")
    ping_exit_code = run_youtube_ping(settings)
    if ping_exit_code != 0:
        logger.error(
            "eu-server-agent ping check failed with exit status %d",
            ping_exit_code,
        )
        print(
            f"eu-server-agent ping check failed with exit status "
            f"{ping_exit_code}.",
            file=sys.stderr,
        )
        return 6

    logger.info("eu-server-agent ping check passed")
    logger.info("Invoking eu-server-agent Squid deployment")
    deploy_exit_code = deploy_squid(settings)
    if deploy_exit_code != 0:
        logger.error(
            "eu-server-agent Squid deployment failed with exit status %d",
            deploy_exit_code,
        )
        print(
            "eu-server-agent Squid deployment failed with exit status "
            f"{deploy_exit_code}.",
            file=sys.stderr,
        )
        return 7

    logger.info("eu-server-agent Squid deployment passed")
    logger.info("Invoking eu-server-test-agent Squid startup check")
    squid_test_exit_code = check_squid_startup(settings)
    if squid_test_exit_code != 0:
        logger.error(
            "eu-server-test-agent Squid startup check failed with exit "
            "status %d",
            squid_test_exit_code,
        )
        print(
            "eu-server-test-agent Squid startup check failed with exit "
            f"status {squid_test_exit_code}.",
            file=sys.stderr,
        )
        return 8

    logger.info("eu-server-test-agent Squid startup check passed")
    logger.info("Invoking eu-server-test-agent Squid HTTP check")
    squid_http_exit_code = check_squid_http(settings)
    if squid_http_exit_code != 0:
        logger.error(
            "eu-server-test-agent Squid HTTP check failed with exit status %d",
            squid_http_exit_code,
        )
        print(
            "eu-server-test-agent Squid HTTP check failed with exit status "
            f"{squid_http_exit_code}.",
            file=sys.stderr,
        )
        return 9

    logger.info("eu-server-test-agent Squid HTTP check passed")
    logger.info("Invoking eu-server-agent Outline deployment")
    outline_deploy_exit_code = deploy_outline(settings, allow_private_targets=True)
    if outline_deploy_exit_code != 0:
        logger.error(
            "eu-server-agent Outline deployment failed with exit status %d",
            outline_deploy_exit_code,
        )
        print(
            "eu-server-agent Outline deployment failed with exit status "
            f"{outline_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 10

    logger.info("eu-server-agent Outline deployment passed")
    logger.info("Invoking eu-server-test-agent Outline health check")
    outline_health_exit_code = check_outline_health(settings, allow_private_targets=True)
    if outline_health_exit_code != 0:
        logger.error(
            "eu-server-test-agent Outline health check failed with exit "
            "status %d",
            outline_health_exit_code,
        )
        print(
            "eu-server-test-agent Outline health check failed with exit "
            f"status {outline_health_exit_code}.",
            file=sys.stderr,
        )
        return 11

    logger.info("eu-server-test-agent Outline health check passed")
    ru_instance, exit_code = _provision_server(
        "ru-server", ensure_ru_server, settings, failure_code=12, auth_code=12
    )
    if ru_instance is None:
        return exit_code

    logger.info("Creating the Outline access key for the RU feasibility probe")
    access_url = create_outline_user(settings, RU_PROBE_OUTLINE_USER)
    if access_url is None:
        logger.error("Could not create the Outline access key for ru-server")
        print("Could not create the Outline access key for ru-server.", file=sys.stderr)
        return 13

    logger.info("Invoking ru-server-agent Outline client deployment")
    client_deploy_exit_code = deploy_outline_client(settings, access_url)
    if client_deploy_exit_code != 0:
        logger.error(
            "ru-server-agent Outline client deployment failed with exit "
            "status %d",
            client_deploy_exit_code,
        )
        print(
            "ru-server-agent Outline client deployment failed with exit "
            f"status {client_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 16

    logger.info("Invoking ru-server-test-agent Outline client check")
    client_check_exit_code = check_outline_client(settings, access_url)
    if client_check_exit_code != 0:
        logger.error(
            "ru-server-test-agent Outline client check failed with exit "
            "status %d",
            client_check_exit_code,
        )
        print(
            "ru-server-test-agent Outline client check failed with exit "
            f"status {client_check_exit_code}.",
            file=sys.stderr,
        )
        return 17

    logger.info("RU Outline client verified")

    logger.info("Invoking ru-server-agent EU Squid forward deployment")
    forward_deploy_exit_code = deploy_squid_forward(settings, access_url)
    if forward_deploy_exit_code != 0:
        logger.error(
            "ru-server-agent Squid forward deployment failed with exit "
            "status %d",
            forward_deploy_exit_code,
        )
        print(
            "ru-server-agent Squid forward deployment failed with exit "
            f"status {forward_deploy_exit_code}.",
            file=sys.stderr,
        )
        return 18

    logger.info("Invoking ru-server-test-agent Squid forward check")
    forward_check_exit_code = check_squid_forward(settings)
    if forward_check_exit_code != 0:
        logger.error(
            "ru-server-test-agent Squid forward check failed with exit "
            "status %d",
            forward_check_exit_code,
        )
        print(
            "ru-server-test-agent Squid forward check failed with exit "
            f"status {forward_check_exit_code}.",
            file=sys.stderr,
        )
        return 19

    logger.info("RU forward to EU Squid verified")

    print(
        f"{ru_instance.name} is available in project "
        f"{settings.GCP_PROJECT_ID}, zone {settings.GCP_ZONE}."
    )
    logger.info("Test environment preflight and provisioning completed")
    return 0
