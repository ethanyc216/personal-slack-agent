import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "next_release_version.py"
SPEC = importlib.util.spec_from_file_location("next_release_version", MODULE_PATH)
next_release_version = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(next_release_version)


def test_next_release_version_continues_latest_matching_patch():
    assert (
        next_release_version.next_release_version(
            "0.1.0",
            ["v0.1.10", "v0.1.13", "v0.1.15"],
        )
        == "0.1.16"
    )


def test_next_release_version_ignores_other_minor_and_malformed_tags():
    assert (
        next_release_version.next_release_version(
            "0.1.0",
            ["v0.2.99", "0.1.50", "v0.1.7-alpha", "v0.1.9"],
        )
        == "0.1.10"
    )


def test_next_release_version_uses_base_version_without_matching_tags():
    assert next_release_version.next_release_version("0.2.0", ["v0.1.16"]) == "0.2.0"


def test_next_release_version_rejects_non_three_part_base_version():
    with pytest.raises(ValueError, match="Expected base version"):
        next_release_version.next_release_version("0.1", ["v0.1.15"])
