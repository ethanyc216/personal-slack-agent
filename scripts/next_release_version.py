#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(
            f"Expected base version to use three numeric parts like 0.1.0, got {version!r}"
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def read_base_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)
    return str(pyproject["project"]["version"])


def list_git_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def next_release_version(base_version: str, tags: list[str]) -> str:
    major, minor, base_patch = _parse_version(base_version)
    tag_re = re.compile(rf"^v{major}\.{minor}\.(\d+)$")
    matching_patches: list[int] = []

    for tag in tags:
        match = tag_re.fullmatch(tag)
        if match is not None:
            matching_patches.append(int(match.group(1)))

    next_patch = max(matching_patches) + 1 if matching_patches else base_patch
    return f"{major}.{minor}.{next_patch}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the next release version from pyproject.toml and existing vX.Y.Z tags."
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        type=Path,
        help="Path to pyproject.toml. Defaults to ./pyproject.toml.",
    )
    args = parser.parse_args(argv)

    print(next_release_version(read_base_version(args.pyproject), list_git_tags()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
