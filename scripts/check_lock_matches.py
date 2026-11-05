"""Check that every package installed in an image has exactly the version pinned in uv.lock."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

# Installers that ship with the base image are not application dependencies.
BASE_IMAGE_TOOLS = {"pip", "setuptools", "wheel"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions(lock_path: Path) -> dict[str, str]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {normalize(p["name"]): p["version"] for p in lock["package"] if "version" in p}


def installed_versions(freeze_path: Path) -> dict[str, str]:
    out = {}
    for line in freeze_path.read_text(encoding="utf-8").splitlines():
        name, sep, version = line.strip().partition("==")
        if sep:
            out[normalize(name)] = version
    return out


def main(lock_path: str, freeze_path: str) -> int:
    locked = locked_versions(Path(lock_path))
    installed = installed_versions(Path(freeze_path))
    problems = []
    for name, version in sorted(installed.items()):
        if name in BASE_IMAGE_TOOLS:
            continue
        if name not in locked:
            problems.append(f"{name}=={version} is installed but not in the lock")
        elif locked[name] != version:
            problems.append(f"{name}: installed {version}, locked {locked[name]}")
    checked = len([n for n in installed if n not in BASE_IMAGE_TOOLS])
    print(f"checked {checked} installed packages against {len(locked)} locked")
    for p in problems:
        print(p)
    if checked == 0:
        print("no packages found in the image listing")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
