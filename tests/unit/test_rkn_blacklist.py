from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fck_rkn.agents.rkn_blacklist import (
    _replace_sets,
    parse_blacklist,
    parse_github_networks,
    update_firewall,
)


BLACKLIST = """table inet filter {
  set blacklist_v4 {
    type ipv4_addr
    flags interval
    elements = {
      192.0.2.0/24,
      198.51.100.1
    }
  }
  set blacklist_v6 {
    type ipv6_addr
    flags interval
    elements = {
      2001:db9::/48
    }
  }
}"""
GITHUB_META = json.dumps(
    {
        "api": ["198.51.100.0/24", "2001:db8:1::/48"],
        "web": ["203.0.113.0/24", "2001:db8:2::/48"],
    }
).encode()


def rules(with_github: bool = True) -> str:
    github_sets = ""
    if with_github:
        github_sets = """  set github_ipv4 {
    type ipv4_addr
    flags interval
    elements = { 203.0.113.0/24 }
  }
  set github_ipv6 {
    type ipv6_addr
    flags interval
    elements = { 2001:db9:2::/48 }
  }
"""
    return f"""destroy table inet fck_rkn_ru
table inet fck_rkn_ru {{
  set rkn_ipv4 {{
    type ipv4_addr
    flags interval
    elements = {{ 192.0.2.0/24 }}
  }}
  set rkn_ipv6 {{
    type ipv6_addr
    flags interval
    elements = {{ 2001:db9::/48 }}
  }}
{github_sets}  chain input {{
    type filter hook input priority -5; policy drop;
  }}
}}
"""


class RknBlacklistParsingTests(unittest.TestCase):
    def test_parses_non_empty_mixed_blacklist_sets(self) -> None:
        ipv4, ipv6 = parse_blacklist(BLACKLIST)
        self.assertEqual(ipv4, ["192.0.2.0/24", "198.51.100.1/32"])
        self.assertEqual(ipv6, ["2001:db9::/48"])

    def test_rejects_empty_invalid_or_wrong_family_blacklist(self) -> None:
        bad = (
            BLACKLIST.replace("192.0.2.0/24,", "invalid,"),
            BLACKLIST.replace(
                "192.0.2.0/24,\n      198.51.100.1", ""
            ),
            BLACKLIST.replace("2001:db9::/48", "192.0.2.1"),
            BLACKLIST.replace("set blacklist_v6", "set absent_v6"),
        )
        for content in bad:
            with self.subTest(content=content[:80]), self.assertRaises(ValueError):
                parse_blacklist(content)

    def test_parses_and_collapses_both_github_meta_categories(self) -> None:
        values = parse_github_networks(GITHUB_META)
        self.assertEqual(
            values,
            [
                "198.51.100.0/24",
                "203.0.113.0/24",
                "2001:db8:1::/48",
                "2001:db8:2::/48",
            ],
        )

    def test_rejects_incomplete_or_invalid_github_meta(self) -> None:
        for metadata in (
            b"not json",
            b'{"api": [], "web": ["203.0.113.0/24"]}',
            b'{"api": ["198.51.100.0/24"], "web": ["bad"]}',
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                parse_github_networks(metadata)


class RknBlacklistActivationTests(unittest.TestCase):
    def test_replaces_all_sets_in_one_candidate(self) -> None:
        candidate = _replace_sets(
            rules(),
            ["192.0.2.0/24"],
            ["2001:db9::/48"],
            [
                "198.51.100.0/24",
                "203.0.113.0/24",
                "2001:db8:1::/48",
                "2001:db8:2::/48",
            ],
        )
        self.assertIn("elements = { 198.51.100.0/24, 203.0.113.0/24 }", candidate)
        self.assertIn(
            "elements = { 2001:db8:1::/48, 2001:db8:2::/48 }", candidate
        )
        self.assertEqual(candidate.count("set rkn_"), 2)

    def test_successful_update_activates_and_saves_full_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rules_file = Path(directory) / "ru-firewall.nft"
            lock_file = Path(directory) / "update.lock"
            original = rules()
            rules_file.write_text(original, encoding="utf-8")
            with (
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_blacklist",
                    return_value=(["192.0.2.0/24", "198.51.100.0/24"], ["2001:db9::/48"]),
                ),
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_github_networks",
                    return_value=[
                        "203.0.113.0/24",
                        "2001:db8:1::/48",
                        "2001:db8:2::/48",
                    ],
                ),
                patch(
                    "fck_rkn.agents.rkn_blacklist.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as run,
            ):
                update_firewall(str(rules_file), str(lock_file), "ru")

            saved = rules_file.read_text(encoding="utf-8")
            self.assertIn("192.0.2.0/24, 198.51.100.0/24", saved)
            self.assertIn("2001:db8:1::/48, 2001:db8:2::/48", saved)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][1], "--check")
            self.assertEqual(run.call_args_list[1].args[0][1], "--file")

    def test_failed_nft_activation_preserves_saved_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rules_file = Path(directory) / "eu-firewall.nft"
            lock_file = Path(directory) / "update.lock"
            original = rules(with_github=False)
            rules_file.write_text(original, encoding="utf-8")
            with (
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_blacklist",
                    return_value=(["192.0.2.0/24"], ["2001:db9::/48"]),
                ),
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_github_networks",
                    side_effect=AssertionError("EU updater must not fetch GitHub ranges"),
                ),
                patch(
                    "fck_rkn.agents.rkn_blacklist.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.CompletedProcess([], 1, "", "test nft failure"),
                    ],
                ),
                self.assertRaisesRegex(RuntimeError, "test nft failure"),
            ):
                update_firewall(str(rules_file), str(lock_file), "eu")
            self.assertEqual(rules_file.read_text(encoding="utf-8"), original)
            self.assertEqual(
                list(Path(directory).glob(".eu-firewall.nft.*")), []
            )

    def test_rejects_github_range_overlapping_blacklist_before_nft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rules_file = Path(directory) / "ru-firewall.nft"
            lock_file = Path(directory) / "update.lock"
            rules_file.write_text(rules(), encoding="utf-8")
            with (
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_blacklist",
                    return_value=(["192.0.2.0/24"], ["2001:db9::/48"]),
                ),
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_github_networks",
                    return_value=["192.0.2.128/25", "2001:db8:1::/48"],
                ),
                patch("fck_rkn.agents.rkn_blacklist.subprocess.run") as run,
                self.assertRaisesRegex(ValueError, "overlap"),
            ):
                update_firewall(str(rules_file), str(lock_file), "ru")
            run.assert_not_called()

    def test_invalid_blacklist_response_preserves_previous_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rules_file = Path(directory) / "eu-firewall.nft"
            lock_file = Path(directory) / "update.lock"
            original = rules(with_github=False)
            rules_file.write_text(original, encoding="utf-8")
            with (
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_blacklist",
                    side_effect=ValueError("empty blacklist"),
                ),
                patch("fck_rkn.agents.rkn_blacklist.fetch_github_networks") as github,
                patch("fck_rkn.agents.rkn_blacklist.subprocess.run") as run,
                self.assertRaisesRegex(ValueError, "empty blacklist"),
            ):
                update_firewall(str(rules_file), str(lock_file), "eu")
            self.assertEqual(rules_file.read_text(encoding="utf-8"), original)
            github.assert_not_called()
            run.assert_not_called()

    def test_saved_file_failure_rolls_back_active_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rules_file = Path(directory) / "eu-firewall.nft"
            lock_file = Path(directory) / "update.lock"
            original = rules(with_github=False)
            rules_file.write_text(original, encoding="utf-8")
            with (
                patch(
                    "fck_rkn.agents.rkn_blacklist.fetch_blacklist",
                    return_value=(["192.0.2.0/24"], ["2001:db9::/48"]),
                ),
                patch("fck_rkn.agents.rkn_blacklist.os.replace", side_effect=OSError),
                patch(
                    "fck_rkn.agents.rkn_blacklist.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as run,
                self.assertRaisesRegex(RuntimeError, "active rules were restored"),
            ):
                update_firewall(str(rules_file), str(lock_file), "eu")
            self.assertEqual(rules_file.read_text(encoding="utf-8"), original)
            self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
