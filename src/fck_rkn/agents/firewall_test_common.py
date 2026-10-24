"""Shared checks for firewall behavior configured on either server."""

from __future__ import annotations

import logging

from fck_rkn.agents.firewall_common import firewall_blacklist_updater_check_command
from fck_rkn.remote.runner import RemoteRunner, execute


logger = logging.getLogger(__name__)
BLACKLIST_CHECK_TIMEOUT_SECONDS = 180


def check_rkn_blacklist_updater(runner: RemoteRunner, host: str) -> int:
    """Verify the daily updater timer and active IPv4/IPv6 crawler sets."""
    command = firewall_blacklist_updater_check_command(host)
    logger.info("Checking the daily RKN blacklist updater on %s-server", host)
    return execute(
        runner,
        command,
        f"RKN blacklist updater check on {host}-server",
        BLACKLIST_CHECK_TIMEOUT_SECONDS,
        print_output=True,
        print_stderr=True,
    )
