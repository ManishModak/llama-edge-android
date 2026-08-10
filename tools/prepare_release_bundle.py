#!/usr/bin/env python3
"""Create a provenance-bound GitHub release bundle without publishing it."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APK = ROOT / "app/build/outputs/apk/release/app-release.apk"
DEFAULT_VERSION = "v1.0.0-arm-challenge"
# `output()` strips the leading porcelain status column. The remaining `M`
# is the submodule worktree modification produced by the verified patch series.
ALLOWED_PATCHED_SUBMODULE_STATUS = "M third_party/llama.cpp"


class ReleaseBundleError(RuntimeError):
    pass


def output(*command: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise ReleaseBundleError(f"{' '.join(command)}: {detail}")
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_tree(allow_dirty: bool) -> tuple[list[str], bool]:
    status = output("git", "status", "--porcelain=v1").splitlines()
    unexpected = [line for line in status if line != ALLOWED_PATCHED_SUBMODULE_STATUS]
    if unexpected and not allow_dirty:
        raise ReleaseBundleError(
            "release tree has uncommitted project changes; commit them before packaging"
        )
    patch_check = subprocess.run(
        [sys.executable, str(ROOT / "tools/apply_llama_patch.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if patch_check.returncode != 0:
        raise ReleaseBundleError(
            patch_check.stderr.strip()
            or patch_check.stdout.strip()
            or "llama.cpp patch verification failed"
        )
    return status, not unexpected


def inspect_build(apk: Path, ndk: Path) -> dict[str, object]:
    encoded = output(
        sys.executable,
        str(ROOT / "tools/inspect_android_build.py"),
        "--apk",
        str(apk),
        "--ndk",
        str(ndk),
    )
    report = json.loads(encoded)
    if report.get("apk", {}).get("sha256") != sha256(apk):
        raise ReleaseBundleError("build inspection APK hash does not match the selected APK")
    return report


def verify_embedded_commit(apk: Path, commit: str) -> str:
    short_commit = commit[:12]
    needle = short_commit.encode("ascii")
    with zipfile.ZipFile(apk) as bundle:
        dex_entries = sorted(
            name for name in bundle.namelist()
            if name.startswith("classes") and name.endswith(".dex")
        )
        if not dex_entries:
            raise ReleaseBundleError("APK contains no classes dex")
        if not any(needle in bundle.read(name) for name in dex_entries):
            raise ReleaseBundleError(
                f"APK does not embed current project commit {short_commit}; rebuild it"
            )
    return short_commit


def llvm_notice(ndk: Path) -> Path:
    candidates = sorted((ndk / "toolchains/llvm/prebuilt").glob("*/NOTICE"))
    if len(candidates) != 1:
        raise ReleaseBundleError(f"expected one NDK LLVM NOTICE, found {len(candidates)}")
    return candidates[0]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_checksums(directory: Path) -> None:
    files = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [f"{sha256(path)}  {path.name}" for path in files]
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_bundle(
    apk: Path,
    ndk: Path,
    apksigner: Path,
    destination: Path,
    version: str,
    allow_dirty: bool,
) -> Path:
    if destination.exists():
        raise ReleaseBundleError(f"destination already exists: {destination}")
    if not apk.is_file():
        raise ReleaseBundleError(f"release APK not found: {apk}")
    if not apksigner.is_file():
        raise ReleaseBundleError(f"apksigner not found: {apksigner}")
    if not version or any(char.isspace() for char in version):
        raise ReleaseBundleError("release version must be non-empty and contain no whitespace")

    commit = output("git", "rev-parse", "HEAD")
    embedded_commit = verify_embedded_commit(apk, commit)
    status, packaging_eligible = validate_release_tree(allow_dirty)
    inspection = inspect_build(apk, ndk)
    signature = output(str(apksigner), "verify", "--verbose", str(apk))
    branch = output("git", "branch", "--show-current") or "detached"
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mobilespec-release-", dir=parent) as temporary:
        staging = Path(temporary)
        apk_name = f"mobilespec-{version}-arm64-v8a.apk"
        shutil.copy2(apk, staging / apk_name)
        shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
        shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", staging / "THIRD_PARTY_NOTICES.md")
        shutil.copy2(llvm_notice(ndk), staging / "LLVM-NOTICE.txt")
        write_json(staging / "build-inspection.json", inspection)
        (staging / "apk-signature.txt").write_text(signature + "\n", encoding="utf-8")
        manifest = {
            "schemaVersion": 1,
            "version": version,
            "createdAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "projectCommit": commit,
            "branch": branch,
            # This covers source/package hygiene only. It does not imply that
            # device qualification or submission gates passed.
            "packagingEligible": packaging_eligible,
            "allowDirtyOverride": allow_dirty,
            "workingTreeStatus": status,
            "apk": {"file": apk_name, "sha256": sha256(staging / apk_name)},
            "apkEmbeddedCommit": embedded_commit,
            "buildInspection": "build-inspection.json",
            "signatureEvidence": "apk-signature.txt",
            "license": "LICENSE",
            "thirdPartyNotices": "THIRD_PARTY_NOTICES.md",
            "llvmNotice": "LLVM-NOTICE.txt",
        }
        write_json(staging / "release-manifest.json", manifest)
        write_checksums(staging)
        Path(temporary).replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="create a development bundle marked packagingEligible=false",
    )
    args = parser.parse_args()
    try:
        result = build_bundle(
            args.apk.resolve(),
            args.ndk.resolve(),
            args.apksigner.resolve(),
            args.output.resolve(),
            args.version,
            args.allow_dirty,
        )
    except (ReleaseBundleError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
