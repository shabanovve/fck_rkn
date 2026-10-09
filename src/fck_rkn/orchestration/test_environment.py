"""Command-line entry point for the fck-rkn-test environment orchestrator."""

from __future__ import annotations

import logging
import sys

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from fck_rkn.agents.eu_server import deploy_outline, deploy_squid, run_youtube_ping
from fck_rkn.agents.eu_server_test import (
    check_outline_health,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.config import DotenvSyntaxError, TestEnvironmentSettings, load_config
from fck_rkn.gcp.compute import ensure_eu_server, validate_project_access


logger = logging.getLogger(__name__)


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

    try:
        instance = ensure_eu_server(settings)
    except GoogleAPICallError as error:
        logger.error("GCP eu-server provisioning failed")
        print("GCP eu-server provisioning failed.", file=sys.stderr)
        print(f"  Error type: {type(error).__name__}", file=sys.stderr)
        if error.code is not None:
            print(f"  HTTP status: {error.code}", file=sys.stderr)
        print(f"  Details: {error.message or error}", file=sys.stderr)
        return 5
    except GoogleAuthError:
        logger.error("GCP authentication failed during VM provisioning")
        print(
            "GCP authentication failed during eu-server provisioning. "
            "Verify the active gcloud account and ADC credentials.",
            file=sys.stderr,
        )
        return 3
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.error("eu-server provisioning failed: %s", error)
        print(f"eu-server provisioning failed: {error}", file=sys.stderr)
        return 5

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
    outline_deploy_exit_code = deploy_outline(settings)
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
    outline_health_exit_code = check_outline_health(settings)
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
    logger.info("Test environment preflight and provisioning completed")
    return 0
