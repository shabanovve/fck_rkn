"""EU-only production deployment entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from fck_rkn.agents.eu_server import (
    deploy_mtproxy,
    deploy_outline,
    apply_eu_firewall,
    create_outline_user,
    deploy_squid,
)
from fck_rkn.agents.ru_server import (
    apply_ru_firewall,
    deploy_mtproxy_forward,
    deploy_outline_client,
    deploy_outline_server,
    deploy_squid_forward,
)
from fck_rkn.agents.ru_server_test import (
    check_mtproxy_forward as check_ru_mtproxy_forward,
    check_outline_client as check_ru_outline_client,
    check_outline_health as check_ru_outline_health,
    check_squid_forward as check_ru_squid_forward,
)
from fck_rkn.agents.outline import load_outline_image
from fck_rkn.agents.eu_server_test import (
    check_eu_firewall_readonly,
    check_mtproxy,
    check_outline_health,
    check_squid_blocking,
    check_squid_http,
    check_squid_startup,
)
from fck_rkn.agents.firewall_test_common import check_rkn_blacklist_updater
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
from fck_rkn.production_firewall import FirewallInputError, prepare_eu_firewall_input
from fck_rkn.production_secrets import LocalSecretStore, SecretStoreError
from fck_rkn.production_setup import ProductionSetupError, configure_interactively
from fck_rkn.remote.runner import RemoteRunner
from fck_rkn.remote.ssh import SshRunner


logger = logging.getLogger(__name__)
RU_PROBE_OUTLINE_USER = "ru-server-feasibility-probe"
RU_PROBE_KEY_SECRET = "ru-outline-access-url"


def create_eu_runner(settings: EuProductionSettings) -> RemoteRunner:
    return SshRunner(
        host=settings.EU_SERVER_ADDRESS,
        user=settings.SSH_USER,
        key_path=settings.SSH_KEY_PATH,
        port=settings.SSH_PORT,
    )


def validate_ru_settings(settings: EuProductionSettings) -> None:
    """Validate that all required Phase 2B settings are present."""
    missing = [
        field
        for field in (
            "RU_SERVER_ADDRESS",
            "RU_METADATA_ADDRESS",
            "RU_DHCP_SERVER_ADDRESS",
            "RU_DNS_ADDRESSES",
            "RU_NTP_ADDRESSES",
            "RU_LOCAL_NETWORKS",
            "RU_PROVIDER_NETWORKS",
        )
        if getattr(settings, field) is None
    ]
    if missing:
        details = "\n".join(
            f"- {field}: is required for Phase 2B" for field in sorted(missing)
        )
        raise ProductionConfigError(
            f"Missing required RU production settings:\n{details}"
        )


def create_ru_runner(settings: EuProductionSettings) -> RemoteRunner:
    """Construct an SshRunner for ru-server after validating RU settings."""
    validate_ru_settings(settings)
    assert settings.RU_SERVER_ADDRESS is not None
    return SshRunner(
        host=settings.RU_SERVER_ADDRESS,
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


def load_ru_outline_image(runner: RemoteRunner) -> int:
    """Load the verified Outline artifact on RU; unverified archives never leave."""
    archive = retrieve_outline_artifact()
    if archive is None:
        return 1
    return load_outline_image(runner, archive)


def ru_outline_image_steps(runner: RemoteRunner) -> list[Step]:
    return [("RU Outline image load", lambda: load_ru_outline_image(runner))]


def ru_outline_deploy_steps(runner: RemoteRunner, ru_address: str) -> list[Step]:
    return [("RU Outline deployment", lambda: deploy_outline_server(runner, ru_address))]


def ru_outline_steps(runner: RemoteRunner, ru_address: str) -> list[Step]:
    return [
        ("RU Outline deployment", lambda: deploy_outline_server(runner, ru_address)),
        ("RU Outline health check", lambda: check_ru_outline_health(runner)),
    ]


def ru_outline_client_deploy_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU Outline client deployment",
            lambda: deploy_outline_client(runner, access_url),
        )
    ]


def ru_outline_client_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU Outline client deployment",
            lambda: deploy_outline_client(runner, access_url),
        ),
        (
            "RU Outline client check",
            lambda: check_ru_outline_client(runner, access_url),
        ),
    ]


def ru_squid_forward_deploy_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU Squid forward deployment",
            lambda: deploy_squid_forward(runner, access_url),
        )
    ]


def ru_squid_forward_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU Squid forward deployment",
            lambda: deploy_squid_forward(runner, access_url),
        ),
        (
            "RU Squid forward check",
            lambda: check_ru_squid_forward(runner),
        ),
    ]


def ru_mtproxy_forward_deploy_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU MTProxy forward deployment",
            lambda: deploy_mtproxy_forward(runner, access_url),
        )
    ]


def ru_mtproxy_forward_steps(
    runner: RemoteRunner, access_url: str
) -> list[Step]:
    return [
        (
            "RU MTProxy forward deployment",
            lambda: deploy_mtproxy_forward(runner, access_url),
        ),
        (
            "RU MTProxy forward check",
            lambda: check_ru_mtproxy_forward(runner),
        ),
    ]


def outline_image_steps(runner: RemoteRunner) -> list[Step]:
    return [("Outline image load", lambda: load_eu_outline_image(runner))]


def outline_steps(runner: RemoteRunner, private_address: str) -> list[Step]:
    return [
        ("Outline deployment", lambda: deploy_outline(runner, private_address)),
        ("Outline health check", lambda: check_outline_health(runner)),
    ]


def mtproxy_steps(runner: RemoteRunner) -> list[Step]:
    return [
        ("MTProxy deployment", lambda: deploy_mtproxy(runner)),
        ("MTProxy check", lambda: check_mtproxy(runner)),
    ]


def provision_ru_outline_key(
    runner: RemoteRunner, store: LocalSecretStore | None = None
) -> int:
    """Create or reuse the EU key for the RU stage and keep it in the local store."""
    access_url = create_outline_user(runner, RU_PROBE_OUTLINE_USER)
    if access_url is None:
        return 1
    try:
        (store or LocalSecretStore()).write(RU_PROBE_KEY_SECRET, access_url)
    except SecretStoreError as error:
        logger.error("Could not store the RU-stage Outline key: %s", error)
        return 1
    logger.info("The RU-stage Outline key is stored in the protected local store")
    return 0


def retrieve_ru_outline_key(store: LocalSecretStore | None = None) -> str | None:
    """Read the dedicated EU Outline access key stored by task 224 for RU."""
    try:
        key = (store or LocalSecretStore()).read(RU_PROBE_KEY_SECRET)
    except SecretStoreError as error:
        logger.error("Could not read the RU-stage Outline key: %s", error)
        return None
    if not key:
        logger.error("The RU-stage Outline key is missing from the secret store")
        return None
    return key


def ru_key_steps(runner: RemoteRunner) -> list[Step]:
    return [("RU-stage Outline key", lambda: provision_ru_outline_key(runner))]


def firewall_steps(
    runner: RemoteRunner, settings: EuProductionSettings
) -> list[Step]:
    prepared: dict[str, str] = {}

    def prepare() -> int:
        try:
            prepared["config"] = prepare_eu_firewall_input(settings)
        except FirewallInputError as error:
            logger.error("Preparing the EU firewall input failed: %s", error)
            return 1
        return 0

    return [
        ("EU firewall input preparation", prepare),
        (
            "EU firewall application",
            lambda: apply_eu_firewall(runner, prepared["config"]),
        ),
        (
            "EU firewall verification",
            lambda: check_eu_firewall_readonly(runner, prepared["config"]),
        ),
        (
            "EU RKN blacklist updater check",
            lambda: check_rkn_blacklist_updater(runner, "eu"),
        ),
    ]


def ru_firewall_steps(
    runner: RemoteRunner, settings: EuProductionSettings, access_url: str
) -> list[Step]:
    from fck_rkn.production_firewall import prepare_ru_firewall_input
    prepared: dict[str, str] = {}

    def prepare() -> int:
        try:
            prepared["config"] = prepare_ru_firewall_input(settings, access_url)
        except FirewallInputError as error:
            logger.error("Preparing the RU firewall input failed: %s", error)
            return 1
        return 0

    return [
        ("RU firewall input preparation", prepare),
        (
            "RU firewall application",
            lambda: apply_ru_firewall(runner, prepared["config"]),
        ),
    ]


def eu_stage_steps(runner: RemoteRunner, settings: EuProductionSettings) -> list[Step]:
    return (
        squid_steps(runner)
        + outline_image_steps(runner)
        + outline_steps(runner, settings.EU_SERVER_ADDRESS)
        + mtproxy_steps(runner)
        + ru_key_steps(runner)
    )


def deploy_eu_squid(runner: RemoteRunner) -> int:
    """Deploy EU Squid and stop if any of its read-only checks fails."""
    return run_steps(squid_steps(runner), "EU Squid deployment and checks completed")


def run_eu_stage(runner: RemoteRunner, settings: EuProductionSettings) -> int:
    steps = eu_stage_steps(runner, settings)
    return run_steps(
        steps,
        f"EU Phase 2A service checks completed on eu-server: "
        f"all {len(steps)} steps and checks passed",
    )


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
        return run_eu_stage(runner, settings)
    return deploy_eu_squid(runner)


if __name__ == "__main__":
    raise SystemExit(main())
