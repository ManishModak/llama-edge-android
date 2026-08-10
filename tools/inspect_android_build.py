#!/usr/bin/env python3
"""Print reproducible identity and backend evidence for an Android app build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APK = ROOT / "app/build/outputs/apk/debug/app-debug.apk"


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def sha256(path: Path) -> str:
    return file_digest(path, "sha256")


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def output(*command: str) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip()


def git_revision(path: Path) -> str:
    return output("git", "-C", str(path), "rev-parse", "HEAD")


def kleidiai_provenance(has_symbols: bool) -> dict[str, object]:
    cmake_path = ROOT / "third_party/llama.cpp/ggml/src/ggml-cpu/CMakeLists.txt"
    cmake = cmake_path.read_text(encoding="utf-8")

    def required(pattern: str, label: str) -> str:
        match = re.search(pattern, cmake)
        if match is None:
            raise RuntimeError(f"could not find KleidiAI {label} in {cmake_path}")
        return match.group(1)

    version = required(r'set\(KLEIDIAI_COMMIT_TAG\s+"([^"]+)"\)', "version")
    url_template = required(r'set\(KLEIDIAI_DOWNLOAD_URL\s+"([^"]+)"\)', "URL")
    expected_md5 = required(
        r'set\(KLEIDIAI_RELEASE_ARCHIVE_MD5\s+"([0-9a-f]+)"\)',
        "archive MD5",
    )
    url = url_template.replace("${KLEIDIAI_COMMIT_TAG}", version)
    archives = sorted(
        (ROOT / "engine-llama/.cxx").glob(f"**/kleidiai-{version}-src.tar.gz"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    archive_report = None
    if archives:
        archive = archives[-1]
        actual_md5 = file_digest(archive, "md5")
        with tarfile.open(archive, "r:gz") as bundle:
            licenses = sorted(
                member.name
                for member in bundle.getmembers()
                if "/LICENSES/" in member.name and member.isfile()
            )
        archive_report = {
            "path": display_path(archive),
            "md5": actual_md5,
            "md5MatchesPinnedSource": actual_md5 == expected_md5,
            "sha256": sha256(archive),
            "licenseFiles": licenses,
        }

    return {
        "compiledSymbolsPresent": has_symbols,
        "version": version,
        "sourceUrl": url,
        "expectedArchiveMd5": expected_md5,
        "downloadedArchive": archive_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path)
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument(
        "--ndk",
        type=Path,
        default=Path(os.environ.get("ANDROID_NDK_HOME", ROOT / ".missing-ndk")),
    )
    args = parser.parse_args()
    if args.library is None:
        libraries = sorted(
            (ROOT / "engine-llama/build/intermediates/cxx/Release").glob(
                "*/obj/arm64-v8a/libmobilespec_llama.so",
            ),
        )
        if not libraries:
            parser.error("native library not found; build the app or pass --library")
        args.library = max(libraries, key=lambda path: path.stat().st_mtime_ns)

    for path in (args.library, args.apk):
        if not path.is_file():
            parser.error(f"build artifact not found: {path}")
    tool_root = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin"
    nm = tool_root / "llvm-nm"
    readelf = tool_root / "llvm-readelf"
    glslc = args.ndk / "shader-tools/linux-x86_64/glslc"
    for path in (nm, readelf, glslc):
        if not path.is_file():
            parser.error(f"NDK tool not found: {path}; pass --ndk")

    symbols = output(str(nm), "-D", str(args.library))
    dynamic = output(str(readelf), "-d", str(args.library))
    has_kleidiai_symbols = " kai_" in symbols
    project_status = output("git", "-C", str(ROOT), "status", "--porcelain")
    project_diff = output("git", "-C", str(ROOT), "diff", "--binary", "HEAD", "--")
    report = {
        "schemaVersion": 1,
        "apk": {"path": display_path(args.apk), "sha256": sha256(args.apk)},
        "nativeLibrary": {
            "path": display_path(args.library),
            "sha256": sha256(args.library),
            "hasKleidiAISymbols": has_kleidiai_symbols,
            "hasVulkanBackendSymbols": "ggml_backend_vk_" in symbols,
            "linksVulkanLoader": "libvulkan.so" in dynamic,
        },
        "kleidiAI": kleidiai_provenance(has_kleidiai_symbols),
        "toolchain": {
            "ndk": args.ndk.name,
            "glslc": output(str(glslc), "--version").splitlines()[0],
        },
        "source": {
            "projectCommit": git_revision(ROOT),
            "projectDirty": bool(project_status),
            "projectTrackedDiffSha256": sha256_text(project_diff),
            "llamaCommit": git_revision(ROOT / "third_party/llama.cpp"),
            "vulkanHeadersCommit": git_revision(ROOT / "third_party/Vulkan-Headers"),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
