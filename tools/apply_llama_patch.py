#!/usr/bin/env python3
"""Check or idempotently apply the repository's pinned llama.cpp patches."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LLAMA_CPP_DIR = REPO_ROOT / "third_party" / "llama.cpp"
DEFAULT_PATCH_DIR = REPO_ROOT / "patches" / "llama.cpp"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def git_apply_check(patch: Path, reverse: bool = False) -> bool:
    command = ["git", "-C", str(LLAMA_CPP_DIR), "apply", "--check"]
    if reverse:
        command.append("--reverse")
    command.append(str(patch))
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or apply the repository's pinned llama.cpp patch series."
    )
    parser.add_argument("--apply", action="store_true", help="apply when not already applied")
    parser.add_argument(
        "--patch",
        type=Path,
        action="append",
        default=None,
        help="specific patch to process (repeatable); default: every *.patch in order",
    )
    args = parser.parse_args()

    patches = (
        [patch.resolve() for patch in args.patch]
        if args.patch
        else sorted(DEFAULT_PATCH_DIR.glob("*.patch"))
    )
    if not patches:
        print(f"error: no patches found under {DEFAULT_PATCH_DIR}", file=sys.stderr)
        return 2
    missing = [patch for patch in patches if not patch.is_file()]
    if missing:
        print(f"error: patch not found: {missing[0]}", file=sys.stderr)
        return 2
    if not LLAMA_CPP_DIR.is_dir():
        print(f"error: llama.cpp checkout not found: {LLAMA_CPP_DIR}", file=sys.stderr)
        return 2

    needs_apply: list[Path] = []
    for patch in patches:
        if git_apply_check(patch, reverse=True):
            print(f"already applied: {display_path(patch)}")
        elif git_apply_check(patch):
            needs_apply.append(patch)
            print(f"applicable: {display_path(patch)}")
        else:
            print(
                f"error: {display_path(patch)} is neither cleanly applicable nor "
                "already applied; inspect the llama.cpp worktree",
                file=sys.stderr,
            )
            return 2

    if not args.apply:
        if needs_apply:
            print("run again with --apply to modify the pinned checkout")
        return 0

    for patch in needs_apply:
        result = subprocess.run(
            ["git", "-C", str(LLAMA_CPP_DIR), "apply", str(patch)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr.strip() or "git apply failed", file=sys.stderr)
            return result.returncode
        print(f"applied: {display_path(patch)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
