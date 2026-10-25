"""EU-only production deployment entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from fck_rkn.agents.eu_server import deploy_squid
from fck_rkn.agents.eu_server_test import (
    check_squid_blocking,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.production_config import (
    ProductionConfigError,
    EuProductionSettings,
    ensure_prod_config,
    load_eu_production_settings,
)
from fck_rkn.remote.runner import RemoteRunner
from fck_rkn.remote.ssh import SshRunner


logger = logging.getLogger(__name__)


def create_eu_runner(settings: EuProductionSettings) -> RemoteRunner:
    return SshRunner(
        host=settings.EU_SERVER_ADDRESS,
        user=settings.SSH_USER,
        key_path=settings.SSH_KEY_PATH,
        port=settings.SSH_PORT,
    )


def deploy_eu_squid(runner: RemoteRunner) -> int:
    """Deploy EU Squid and stop if any of its read-only checks fails."""
    steps = (
        ("Squid deployment", deploy_squid),
        ("Squid startup check", check_squid_startup),
        ("Squid HTTP check", check_squid_http),
        ("Squid blocking check", check_squid_blocking),
    )
    for description, action in steps:
        logger.info("Starting EU production step: %s", description)
        exit_code = action(runner)
        if exit_code != 0:
            logger.error(
                "EU production step %s failed with exit status %d",
                description,
                exit_code,
            )
            return exit_code
    logger.info("EU Squid deployment and checks completed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an EU-only production deployment operation."
    )
    parser.add_argument(
        "operation",
        nargs="?",
        default="deploy-eu-squid",
        choices=("deploy-eu-squid",),
        help="Operation to run (default: deploy-eu-squid)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        ensure_prod_config()
        settings = load_eu_production_settings()
    except (OSError, UnicodeError, ProductionConfigError) as error:
        logger.error(
            "Could not load valid production configuration (%s)",
            type(error).__name__,
        )
        return 2

    runner = create_eu_runner(settings)
    return deploy_eu_squid(runner)


if __name__ == "__main__":
    raise SystemExit(main())
