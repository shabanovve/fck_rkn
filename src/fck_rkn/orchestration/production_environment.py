"""EU-only production deployment entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from fck_rkn.agents.eu_server import deploy_squid
from fck_rkn.agents.outline import load_outline_image
from fck_rkn.agents.eu_server_test import (
    check_squid_blocking,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.orchestration.outline_artifact import (
    build_outline_artifact,
    retrieve_outline_artifact,
)
from fck_rkn.production_config import (
    PROD_CONFIG_FILE,
    ProductionConfigError,
    EuProductionSettings,
    ensure_prod_config,
    load_eu_production_settings,
)
from fck_rkn.production_setup import ProductionSetupError, configure_interactively
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


def create_runner(host: str, user: str, key_path: Path, port: int) -> RemoteRunner:
    return SshRunner(host=host, user=user, key_path=key_path, port=port)


def read_input(prompt: str) -> str:
    """Read a line, asking again if the terminal sent invalid UTF-8."""
    while True:
        try:
            return input(prompt)
        except UnicodeDecodeError:
            print("Input was not valid UTF-8; please type it again.")


def run_configuration() -> int:
    try:
        configure_interactively(create_runner, prompt=read_input)
    except ProductionSetupError as error:
        logger.error("Interactive configuration stopped: %s", error)
        return 2
    except (EOFError, KeyboardInterrupt):
        logger.error("Interactive configuration cancelled; nothing was written")
        return 2
    return 0


Step = tuple[str, Callable[[], int]]


def run_steps(steps: Sequence[Step], completion: str) -> int:
    """Run steps in order and stop at the first failure, naming it."""
    for description, action in steps:
        logger.info("Starting EU production step: %s", description)
        exit_code = action()
        if exit_code != 0:
            logger.error(
                "EU production step %s failed with exit status %d",
                description,
                exit_code,
            )
            return exit_code
    logger.info(completion)
    return 0


def squid_steps(runner: RemoteRunner) -> list[Step]:
    return [
        ("Squid deployment", lambda: deploy_squid(runner)),
        ("Squid startup check", lambda: check_squid_startup(runner)),
        ("Squid HTTP check", lambda: check_squid_http(runner)),
        ("Squid blocking check", lambda: check_squid_blocking(runner)),
    ]


def load_eu_outline_image(runner: RemoteRunner) -> int:
    """Load the verified Outline artifact on EU; unverified archives never leave."""
    archive = retrieve_outline_artifact()
    if archive is None:
        return 1
    return load_outline_image(runner, archive)


def outline_image_steps(runner: RemoteRunner) -> list[Step]:
    return [("Outline image load", lambda: load_eu_outline_image(runner))]


def eu_stage_steps(runner: RemoteRunner) -> list[Step]:
    return squid_steps(runner) + outline_image_steps(runner)


def deploy_eu_squid(runner: RemoteRunner) -> int:
    """Deploy EU Squid and stop if any of its read-only checks fails."""
    return run_steps(squid_steps(runner), "EU Squid deployment and checks completed")


def run_eu_stage(runner: RemoteRunner) -> int:
    return run_steps(eu_stage_steps(runner), "EU production stage completed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an EU-only production deployment operation."
    )
    parser.add_argument(
        "operation",
        nargs="?",
        default=None,
        choices=("deploy-eu-squid", "configure", "build-outline-artifact"),
        help=(
            "Single operation to run. Without it, the configuration is "
            "ensured (interactively on a terminal if missing or invalid) and "
            "the EU deployment with its checks runs."
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    if args.operation == "configure":
        return run_configuration()
    if args.operation == "build-outline-artifact":
        return build_outline_artifact()

    interactive = args.operation is None and sys.stdin.isatty()
    if interactive and not PROD_CONFIG_FILE.exists():
        status = run_configuration()
        if status != 0:
            return status

    settings = None
    for attempt in (1, 2):
        try:
            ensure_prod_config()
            settings = load_eu_production_settings()
            break
        except (OSError, UnicodeError, ProductionConfigError) as error:
            logger.error("Could not load valid production configuration:\n%s", error)
            if not interactive or attempt == 2:
                return 2
            status = run_configuration()
            if status != 0:
                return status

    if args.operation is None:
        logger.info("Running the EU production stage; the RU stage is not implemented")
    runner = create_eu_runner(settings)
    if args.operation is None:
        return run_eu_stage(runner)
    return deploy_eu_squid(runner)


if __name__ == "__main__":
    raise SystemExit(main())
