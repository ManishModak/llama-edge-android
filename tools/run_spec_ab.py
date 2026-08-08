#!/usr/bin/env python3
"""Run a same-target speculative-decoding A/B over adb and write JSON evidence.

The harness launches llama-server on the Android device, forwards its HTTP port
through adb, and compares deterministic streamed completions with speculation
disabled and enabled. It supports native MTP and zero-weight ngram-mod suites.
It is stdlib-only and supports Python 3.11+ on Windows and Linux.

No model is downloaded or pushed. All binaries and GGUFs must already exist in
the on-device locations declared by the suite and models/manifest.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shlex
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUITE = REPO_ROOT / "benchmarks" / "suites" / "phase3-mtp.json"
DEFAULT_OUT_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"
LLAMA_CPP_DIR = REPO_ROOT / "third_party" / "llama.cpp"


class HarnessError(RuntimeError):
    """A runtime or validation failure that should be preserved in evidence."""


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def app_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def llama_cpp_source_state() -> dict[str, Any]:
    """Fingerprint the pinned commit and any private-checkout candidate patch."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(LLAMA_CPP_DIR), *args],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HarnessError(f"cannot inspect pinned llama.cpp: {exc}") from exc

    head_result = git("rev-parse", "HEAD")
    if head_result.returncode != 0:
        raise HarnessError(
            f"cannot resolve llama.cpp HEAD: {head_result.stderr.strip()}"
        )
    status_result = git("status", "--short")
    if status_result.returncode != 0:
        raise HarnessError(
            f"cannot inspect llama.cpp status: {status_result.stderr.strip()}"
        )
    diff_result = git("diff", "--binary", "--no-ext-diff")
    if diff_result.returncode != 0:
        raise HarnessError(
            f"cannot fingerprint llama.cpp diff: {diff_result.stderr.strip()}"
        )
    diff_bytes = diff_result.stdout.encode("utf-8")
    status_lines = [line for line in status_result.stdout.splitlines() if line]
    return {
        "commit": head_result.stdout.strip(),
        "dirty": bool(status_lines),
        "status": status_lines,
        "worktreeDiffSha256": (
            hashlib.sha256(diff_bytes).hexdigest() if diff_bytes else None
        ),
    }


def candidate_patch_applied(patch: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(LLAMA_CPP_DIR),
                "apply",
                "--reverse",
                "--check",
                str(patch),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"cannot check candidate patch state: {exc}") from exc
    return result.returncode == 0


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{path} must contain a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise HarnessError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def prompt_provenance(prompts: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for prompt in prompts:
        canonical = json.dumps(
            prompt["messages"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        rows.append(
            {
                "id": prompt["id"],
                "category": prompt["category"],
                "messagesCanonicalSha256": hashlib.sha256(canonical).hexdigest(),
            }
        )
    return rows


def load_manifest() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = load_json(REPO_ROOT / "models" / "manifest.json")
    model_map: dict[str, dict[str, Any]] = {}
    for model in manifest.get("models", []):
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            raise HarnessError("every manifest model must be an object with a string id")
        if model["id"] in model_map:
            raise HarnessError(f"duplicate manifest model id: {model['id']}")
        model_map[model["id"]] = model
    return manifest, model_map


def _require_int(container: dict[str, Any], name: str, minimum: int = 0) -> int:
    value = container.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise HarnessError(f"{name} must be an integer >= {minimum}")
    return value


def validate_suite(
    suite: dict[str, Any], model_map: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if suite.get("schemaVersion") != 1:
        raise HarnessError("suite schemaVersion must be 1")
    if not isinstance(suite.get("suite"), str) or not suite["suite"]:
        raise HarnessError("suite must have a non-empty name")

    defaults = suite.get("defaults")
    if not isinstance(defaults, dict):
        raise HarnessError("suite defaults must be an object")
    for name in ("binDir", "modelsDir", "binary", "targetModel", "llamaCppBaseCommit"):
        if not isinstance(defaults.get(name), str) or not defaults[name]:
            raise HarnessError(f"defaults.{name} must be a non-empty string")
    common_integers = (
        ("contextSize", 1),
        ("threads", 1),
        ("threadsBatch", 1),
        ("nGpuLayers", 0),
        ("repetitions", 1),
        ("generatedTokens", 1),
        ("remotePort", 1),
        ("startupTimeoutSeconds", 1),
        ("requestTimeoutSeconds", 1),
        ("telemetryIntervalSeconds", 1),
    )
    for name, minimum in common_integers:
        _require_int(defaults, name, minimum)

    speculative_mode = defaults.get("speculativeMode", "mtp")
    if speculative_mode not in {"mtp", "ngram"}:
        raise HarnessError("defaults.speculativeMode must be mtp or ngram")
    defaults["speculativeMode"] = speculative_mode
    if speculative_mode == "mtp":
        for name in ("draftModel", "candidatePatch"):
            if not isinstance(defaults.get(name), str) or not defaults[name]:
                raise HarnessError(f"defaults.{name} must be a non-empty string")
        _require_int(defaults, "draftTokens", 1)
    else:
        for name, minimum in (
            ("ngramMatch", 16),
            ("ngramMin", 0),
            ("ngramMax", 1),
        ):
            _require_int(defaults, name, minimum)
        if defaults["ngramMin"] > defaults["ngramMax"]:
            raise HarnessError("defaults.ngramMin must be <= defaults.ngramMax")

    target_id = defaults["targetModel"]
    if target_id not in model_map:
        raise HarnessError(f"target model id not in manifest: {target_id}")
    if speculative_mode == "mtp":
        draft_id = defaults["draftModel"]
        if draft_id not in model_map:
            raise HarnessError(f"draft model id not in manifest: {draft_id}")
        compatible = model_map[draft_id].get("compatibleTargetIds", [])
        if target_id not in compatible:
            raise HarnessError(
                f"draft model {draft_id} does not declare compatibility with {target_id}"
            )

    prompts = suite.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise HarnessError("suite prompts must be a non-empty array")
    prompt_ids: set[str] = set()
    for prompt in prompts:
        if not isinstance(prompt, dict):
            raise HarnessError("every prompt must be an object")
        prompt_id = prompt.get("id")
        messages = prompt.get("messages")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise HarnessError("every prompt must have a non-empty id")
        if prompt_id in prompt_ids:
            raise HarnessError(f"duplicate prompt id: {prompt_id}")
        prompt_ids.add(prompt_id)
        if not isinstance(prompt.get("category"), str) or not prompt["category"]:
            raise HarnessError(f"prompt {prompt_id} must have a category")
        if not isinstance(messages, list) or not messages:
            raise HarnessError(f"prompt {prompt_id} must have non-empty messages")
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in {"system", "user", "assistant"}
                or not isinstance(message.get("content"), str)
            ):
                raise HarnessError(
                    f"prompt {prompt_id} messages need role and string content"
                )

    order = defaults.get("modeOrder", ["baseline", speculative_mode])
    if (
        not isinstance(order, list)
        or set(order) != {"baseline", speculative_mode}
        or len(order) != 2
    ):
        raise HarnessError(
            f"defaults.modeOrder must contain baseline and {speculative_mode} exactly once"
        )
    artifact_file = defaults.get("artifactFile", f"{speculative_mode}-ab.json")
    if (
        not isinstance(artifact_file, str)
        or not artifact_file
        or Path(artifact_file).name != artifact_file
    ):
        raise HarnessError("defaults.artifactFile must be a plain filename")
    defaults["artifactFile"] = artifact_file
    return defaults


class Adb:
    def __init__(self, serial: str | None, dry_run: bool = False):
        self.serial = serial
        self.dry_run = dry_run

    @property
    def base(self) -> list[str]:
        return ["adb"] + (["-s", self.serial] if self.serial else [])

    def run(
        self,
        args: list[str],
        *,
        timeout: float = 60,
        check: bool = True,
        show_dry_run: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self.base + args
        if self.dry_run:
            if show_dry_run:
                print("  $ " + " ".join(command))
            return subprocess.CompletedProcess(command, 0, "", "")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise HarnessError("adb not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(
                f"adb command timed out after {timeout:.0f}s: {' '.join(command)}"
            ) from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise HarnessError(
                f"adb command failed ({result.returncode}): {' '.join(command)}"
                + (f"\n{detail}" if detail else "")
            )
        return result

    def shell(
        self,
        command: str,
        *,
        timeout: float = 60,
        check: bool = True,
        show_dry_run: bool = True,
    ) -> str:
        return self.run(
            ["shell", command],
            timeout=timeout,
            check=check,
            show_dry_run=show_dry_run,
        ).stdout.strip()


def resolve_serial(requested: str | None, dry_run: bool) -> str | None:
    if requested or dry_run:
        return requested
    adb = Adb(None)
    output = adb.run(["devices"]).stdout
    serials = [
        line.split()[0]
        for line in output.splitlines()[1:]
        if line.strip().endswith("\tdevice") or line.strip().endswith(" device")
    ]
    if not serials:
        raise HarnessError("no adb device connected; use --dry-run for a preview")
    if len(serials) > 1:
        raise HarnessError(
            f"multiple adb devices connected ({', '.join(serials)}); pass --serial"
        )
    return serials[0]


def remote_join(directory: str, filename: str) -> str:
    return directory.rstrip("/") + "/" + filename.lstrip("/")


def quote_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def build_server_parts(
    defaults: dict[str, Any],
    target: dict[str, Any],
    draft: dict[str, Any] | None,
    mode: str,
) -> list[str]:
    binary = remote_join(defaults["binDir"], defaults["binary"])
    target_path = remote_join(defaults["modelsDir"], target["file"])
    parts = [
        binary,
        "-m",
        target_path,
        "-c",
        str(defaults["contextSize"]),
        "-t",
        str(defaults["threads"]),
        "-tb",
        str(defaults["threadsBatch"]),
        "-ngl",
        str(defaults["nGpuLayers"]),
        "-np",
        "1",
        "--host",
        "127.0.0.1",
        "--port",
        str(defaults["remotePort"]),
        "--no-webui",
    ]
    if defaults.get("cpuMask"):
        parts += ["-C", str(defaults["cpuMask"])]
    if defaults.get("cpuMaskBatch"):
        parts += ["-Cb", str(defaults["cpuMaskBatch"])]
    if mode == "baseline":
        parts += ["--spec-type", "none"]
    elif mode == "mtp":
        if draft is None:
            raise HarnessError("MTP mode requires a draft model")
        parts += [
            "--model-draft",
            remote_join(defaults["modelsDir"], draft["file"]),
            "--spec-type",
            "draft-mtp",
            "--spec-draft-n-max",
            str(defaults["draftTokens"]),
            "--spec-draft-threads",
            str(defaults.get("draftThreads", defaults["threads"])),
            "--spec-draft-threads-batch",
            str(defaults.get("draftThreadsBatch", defaults["threadsBatch"])),
            "--spec-draft-ngl",
            str(defaults.get("draftGpuLayers", defaults["nGpuLayers"])),
        ]
    elif mode == "ngram":
        parts += [
            "--spec-type",
            "ngram-mod",
            "--spec-ngram-mod-n-match",
            str(defaults["ngramMatch"]),
            "--spec-ngram-mod-n-min",
            str(defaults["ngramMin"]),
            "--spec-ngram-mod-n-max",
            str(defaults["ngramMax"]),
        ]
    else:
        raise HarnessError(f"unsupported mode: {mode}")
    extra = defaults.get("extraServerArgs", [])
    if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
        raise HarnessError("defaults.extraServerArgs must be an array of strings")
    return parts + extra


def preflight(
    adb: Adb,
    defaults: dict[str, Any],
    target: dict[str, Any],
    draft: dict[str, Any] | None,
) -> dict[str, Any]:
    binary = remote_join(defaults["binDir"], defaults["binary"])
    paths: dict[str, str] = {
        "binary": binary,
        "target": remote_join(defaults["modelsDir"], target["file"]),
    }
    models = {"target": target}
    if draft is not None:
        paths["draft"] = remote_join(defaults["modelsDir"], draft["file"])
        models["draft"] = draft
    if adb.dry_run:
        adb.shell(f"test -x {shlex.quote(paths['binary'])}")
        for kind in models:
            path = paths[kind]
            adb.shell(f"test -r {shlex.quote(path)}")
            adb.shell(f"stat -c %s {shlex.quote(path)}")
            adb.shell(f"sha256sum {shlex.quote(path)}")
        adb.shell(
            f"LD_LIBRARY_PATH={shlex.quote(defaults['binDir'])} "
            f"{shlex.quote(paths['binary'])} --version"
        )
        adb.shell(
            f"LD_LIBRARY_PATH={shlex.quote(defaults['binDir'])} "
            f"{shlex.quote(paths['binary'])} --help 2>&1"
        )
        adb.shell(f"sha256sum {shlex.quote(paths['binary'])}")
        return {
            "ok": None,
            "paths": paths,
            "sizesBytes": {},
            "sha256": {},
            "sha256Checks": {},
            "binaryVersion": None,
            "binarySha256": None,
            "requiredFlagChecks": None,
            "dryRun": True,
        }

    sizes: dict[str, int] = {}
    hashes: dict[str, str] = {}
    for kind, path in paths.items():
        test_flag = "-x" if kind == "binary" else "-r"
        adb.shell(f"test {test_flag} {shlex.quote(path)}")
        if kind != "binary":
            raw = adb.shell(f"stat -c %s {shlex.quote(path)}")
            try:
                sizes[kind] = int(raw.splitlines()[-1])
            except (ValueError, IndexError) as exc:
                raise HarnessError(f"could not parse remote file size for {path}: {raw}") from exc
            hash_output = adb.shell(
                f"sha256sum {shlex.quote(path)}",
                timeout=900,
            )
            digest = hash_output.split()[0] if hash_output else ""
            if len(digest) != 64 or any(
                character not in "0123456789abcdefABCDEF" for character in digest
            ):
                raise HarnessError(
                    f"could not parse remote SHA-256 for {path}: {hash_output}"
                )
            hashes[kind] = digest.lower()

    expected = {kind: model.get("sizeBytes") for kind, model in models.items()}
    size_checks: dict[str, bool | None] = {}
    for kind, expected_size in expected.items():
        if isinstance(expected_size, int):
            size_checks[kind] = sizes[kind] == expected_size
            if not size_checks[kind]:
                raise HarnessError(
                    f"{kind} size mismatch: device={sizes[kind]}, manifest={expected_size}"
                )
        else:
            size_checks[kind] = None
    hash_checks: dict[str, bool | None] = {}
    for kind, model in models.items():
        expected_hash = model.get("sha256")
        if isinstance(expected_hash, str) and len(expected_hash) == 64:
            hash_checks[kind] = hashes[kind] == expected_hash.lower()
            if not hash_checks[kind]:
                raise HarnessError(
                    f"{kind} SHA-256 mismatch: device={hashes[kind]}, "
                    f"manifest={expected_hash}"
                )
        else:
            hash_checks[kind] = None
    version = adb.shell(
        f"LD_LIBRARY_PATH={shlex.quote(defaults['binDir'])} "
        f"{shlex.quote(binary)} --version",
        timeout=30,
        check=False,
    )
    help_output = adb.shell(
        f"LD_LIBRARY_PATH={shlex.quote(defaults['binDir'])} "
        f"{shlex.quote(binary)} --help 2>&1",
        timeout=60,
        check=False,
    )
    if defaults["speculativeMode"] == "mtp":
        required_flags = ("--model-draft", "--spec-type", "--spec-draft-n-max")
    else:
        required_flags = (
            "--spec-type",
            "--spec-ngram-mod-n-match",
            "--spec-ngram-mod-n-min",
            "--spec-ngram-mod-n-max",
        )
    required_flag_checks = {flag: flag in help_output for flag in required_flags}
    if not all(required_flag_checks.values()):
        missing = [
            flag for flag, present in required_flag_checks.items() if not present
        ]
        raise HarnessError(
            f"on-device binary lacks required speculative flags: {', '.join(missing)}"
        )
    sha_output = adb.shell(
        f"sha256sum {shlex.quote(binary)}",
        timeout=120,
        check=False,
    )
    binary_sha = sha_output.split()[0] if sha_output else None
    if binary_sha is not None and (
        len(binary_sha) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in binary_sha)
    ):
        binary_sha = None
    return {
        "ok": True,
        "paths": paths,
        "sizesBytes": sizes,
        "sizeChecks": size_checks,
        "sha256": hashes,
        "sha256Checks": hash_checks,
        "binaryVersion": version or None,
        "binarySha256": binary_sha,
        "requiredFlagChecks": required_flag_checks,
        "dryRun": False,
    }


def _parse_key_kb(text: str, key: str) -> int | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(key + ":"):
            fields = stripped.split()
            if len(fields) >= 2:
                try:
                    return int(fields[1])
                except ValueError:
                    return None
    return None


def capture_memory(adb: Adb, pid: int | None) -> dict[str, Any]:
    if adb.dry_run:
        return {
            "capturedAt": now_utc(),
            "memAvailableKb": None,
            "swapFreeKb": None,
            "swapTotalKb": None,
            "processVmRssKb": None,
            "processVmHwmKb": None,
            "zram": None,
        }
    pid_status = f"/proc/{pid}/status" if pid is not None else "/dev/null"
    command = (
        "cat /proc/meminfo; echo __PROC__; "
        f"cat {shlex.quote(pid_status)} 2>/dev/null; echo __ZRAM__; "
        "cat /sys/block/zram0/mm_stat 2>/dev/null"
    )
    output = adb.shell(command, timeout=20, check=False, show_dry_run=False)
    mem_text, _, tail = output.partition("__PROC__")
    proc_text, _, zram_text = tail.partition("__ZRAM__")
    zram_fields = zram_text.strip().split()
    zram = None
    if len(zram_fields) >= 3:
        try:
            zram = {
                "originalDataSizeBytes": int(zram_fields[0]),
                "compressedDataSizeBytes": int(zram_fields[1]),
                "memoryUsedTotalBytes": int(zram_fields[2]),
                "raw": zram_text.strip(),
            }
        except ValueError:
            zram = {"raw": zram_text.strip()}
    return {
        "capturedAt": now_utc(),
        "memAvailableKb": _parse_key_kb(mem_text, "MemAvailable"),
        "swapFreeKb": _parse_key_kb(mem_text, "SwapFree"),
        "swapTotalKb": _parse_key_kb(mem_text, "SwapTotal"),
        "processVmRssKb": _parse_key_kb(proc_text, "VmRSS"),
        "processVmHwmKb": _parse_key_kb(proc_text, "VmHWM"),
        "zram": zram,
    }


def capture_state(adb: Adb, pid: int | None) -> dict[str, Any]:
    memory = capture_memory(adb, pid)
    if adb.dry_run:
        return {
            **memory,
            "batteryLevel": None,
            "batteryTempC": None,
            "thermalStatus": None,
        }
    output = adb.shell(
        "dumpsys battery; echo __THERMAL__; "
        "dumpsys thermalservice | grep -m1 'Thermal Status'",
        timeout=30,
        check=False,
        show_dry_run=False,
    )
    battery, _, thermal = output.partition("__THERMAL__")
    level = _parse_key_kb(battery, "level")
    temp_raw = _parse_key_kb(battery, "temperature")
    return {
        **memory,
        "batteryLevel": level,
        "batteryTempC": temp_raw / 10.0 if temp_raw is not None else None,
        "thermalStatus": thermal.strip() or None,
    }


class TelemetrySampler:
    def __init__(self, adb: Adb, pid: int, interval_seconds: int):
        self.adb = adb
        self.pid = pid
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(capture_memory(self.adb, self.pid))
            except Exception as exc:  # Evidence should survive a telemetry-only failure.
                self.samples.append({"capturedAt": now_utc(), "error": str(exc)})
            self._stop.wait(self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=25)


def choose_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_http_json(url: str, timeout: float) -> tuple[int, dict[str, Any] | None, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, body


def start_server(
    adb: Adb,
    defaults: dict[str, Any],
    parts: list[str],
    mode: str,
    stamp: str,
) -> tuple[int, str, str]:
    log_path = remote_join(defaults["binDir"], f"{stamp}-{mode}-llama-server.log")
    script = (
        f"cd {shlex.quote(defaults['binDir'])} || exit 1; "
        f"export LD_LIBRARY_PATH={shlex.quote(defaults['binDir'])}; "
        f"nohup {quote_command(parts)} > {shlex.quote(log_path)} "
        "2>&1 < /dev/null & echo $!"
    )
    if adb.dry_run:
        adb.shell(script)
        return 0, log_path, quote_command(parts)
    output = adb.shell(script, timeout=30)
    candidates = [line.strip() for line in output.splitlines() if line.strip().isdigit()]
    if not candidates:
        raise HarnessError(f"server launch did not return a pid: {output!r}")
    return int(candidates[-1]), log_path, quote_command(parts)


def stop_server(adb: Adb, pid: int) -> None:
    if pid <= 0:
        return
    adb.shell(f"kill -TERM {pid}", timeout=10, check=False, show_dry_run=False)
    for _ in range(10):
        alive = adb.shell(
            f"kill -0 {pid} 2>/dev/null; echo $?",
            timeout=10,
            check=False,
            show_dry_run=False,
        )
        if alive.splitlines()[-1:] == ["1"]:
            return
        time.sleep(0.5)
    adb.shell(f"kill -KILL {pid}", timeout=10, check=False, show_dry_run=False)


def tail_log(adb: Adb, path: str, lines: int = 200) -> str:
    if adb.dry_run:
        return ""
    return adb.shell(
        f"tail -n {lines} {shlex.quote(path)}",
        timeout=30,
        check=False,
        show_dry_run=False,
    )


def wait_for_health(
    host_port: int,
    timeout_seconds: int,
    alive: Callable[[], bool],
) -> dict[str, Any]:
    url = f"http://127.0.0.1:{host_port}/health"
    deadline = time.monotonic() + timeout_seconds
    last_status: int | None = None
    last_body = ""
    while time.monotonic() < deadline:
        if not alive():
            raise HarnessError("llama-server exited while loading")
        try:
            status, body, raw = read_http_json(url, timeout=3)
            last_status, last_body = status, raw
            if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
                return body
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise HarnessError(
        f"llama-server health timeout after {timeout_seconds}s "
        f"(last status={last_status}, body={last_body[:300]!r})"
    )


def _event_text(event: dict[str, Any]) -> str:
    if isinstance(event.get("content"), str):
        return event["content"]
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            # Thinking-capable chat templates stream hidden reasoning separately
            # from visible content. Preserve both, in wire order, so TTFT and
            # exact-output gates cover every generated byte.
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            return "".join(
                part for part in (reasoning, content) if isinstance(part, str)
            )
        if isinstance(choice.get("text"), str):
            return choice["text"]
    return ""


def _event_finish_reason(event: dict[str, Any]) -> str | None:
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        value = choices[0].get("finish_reason")
        return value if isinstance(value, str) else None
    value = event.get("stop_type")
    return value if isinstance(value, str) else None


def stream_completion(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    start = time.perf_counter()
    first_content_at: float | None = None
    content_parts: list[str] = []
    token_ids: list[int] = []
    timings: dict[str, Any] = {}
    finish_reason: str | None = None
    events = 0
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HarnessError(f"completion HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise HarnessError(f"completion request failed: {exc}") from exc

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise HarnessError(f"invalid SSE JSON: {data[:300]}") from exc
            if not isinstance(event, dict):
                continue
            events += 1
            text = _event_text(event)
            tokens = event.get("tokens")
            has_token = isinstance(tokens, list) and len(tokens) > 0
            if first_content_at is None and (text or has_token):
                first_content_at = time.perf_counter()
            if text:
                content_parts.append(text)
            if isinstance(tokens, list):
                token_ids.extend(token for token in tokens if isinstance(token, int))
            if isinstance(event.get("timings"), dict):
                timings = event["timings"]
            current_finish = _event_finish_reason(event)
            if current_finish:
                finish_reason = current_finish
    end = time.perf_counter()
    content = "".join(content_parts)
    return {
        "content": content,
        "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "tokens": token_ids,
        "events": events,
        "timeToFirstTokenMs": (
            (first_content_at - start) * 1000 if first_content_at is not None else None
        ),
        "endToEndMs": (end - start) * 1000,
        "finishReason": finish_reason,
        "timings": timings,
    }


def request_payload(
    prompt: dict[str, Any], defaults: dict[str, Any], repetition: int
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": prompt["messages"],
        "max_tokens": defaults["generatedTokens"],
        "stream": True,
        "temperature": 0.0,
        "top_k": 1,
        "top_p": 1.0,
        "min_p": 0.0,
        "seed": defaults.get("seed", 42),
        "cache_prompt": False,
        "timings_per_token": False,
    }
    extra = defaults.get("extraRequestFields", {})
    if not isinstance(extra, dict):
        raise HarnessError("defaults.extraRequestFields must be an object")
    payload.update(extra)
    payload["messages"] = prompt["messages"]
    payload["max_tokens"] = defaults["generatedTokens"]
    payload["stream"] = True
    return payload


def device_identity(adb: Adb) -> dict[str, Any]:
    if adb.dry_run:
        for prop in ("ro.product.model", "ro.build.version.release", "ro.soc.model"):
            adb.shell(f"getprop {prop}")
        return {
            "serial": adb.serial,
            "model": None,
            "android": None,
            "soc": None,
        }
    values = {}
    for key, prop in (
        ("model", "ro.product.model"),
        ("android", "ro.build.version.release"),
        ("soc", "ro.soc.model"),
    ):
        values[key] = adb.shell(f"getprop {prop}", timeout=15) or None
    return {"serial": adb.serial, **values}


def summarize_runs(runs: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    selected = [run for run in runs if run.get("mode") == mode and run.get("ok")]
    speeds = [
        float(run["timings"]["predicted_per_second"])
        for run in selected
        if isinstance(run.get("timings", {}).get("predicted_per_second"), (int, float))
    ]
    ttfts = [
        float(run["timeToFirstTokenMs"])
        for run in selected
        if isinstance(run.get("timeToFirstTokenMs"), (int, float))
    ]
    e2e = [
        float(run["endToEndMs"])
        for run in selected
        if isinstance(run.get("endToEndMs"), (int, float))
    ]
    draft_n = sum(int(run.get("timings", {}).get("draft_n") or 0) for run in selected)
    accepted = sum(
        int(run.get("timings", {}).get("draft_n_accepted") or 0)
        for run in selected
    )

    def stats(values: list[float]) -> dict[str, Any]:
        return {
            "mean": statistics.mean(values) if values else None,
            "stddev": statistics.stdev(values) if len(values) >= 2 else 0.0 if values else None,
            "samples": values,
        }

    return {
        "successfulRuns": len(selected),
        "failedRuns": len([run for run in runs if run.get("mode") == mode]) - len(selected),
        "predictedTokensPerSecond": stats(speeds),
        "timeToFirstTokenMs": stats(ttfts),
        "endToEndMs": stats(e2e),
        "draftTokens": draft_n,
        "acceptedDraftTokens": accepted,
        "acceptanceRate": accepted / draft_n if draft_n else None,
    }


def memory_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    mem = [
        sample["memAvailableKb"]
        for sample in samples
        if isinstance(sample.get("memAvailableKb"), int)
    ]
    swap = [
        sample["swapFreeKb"]
        for sample in samples
        if isinstance(sample.get("swapFreeKb"), int)
    ]
    hwm = [
        sample["processVmHwmKb"]
        for sample in samples
        if isinstance(sample.get("processVmHwmKb"), int)
    ]
    zram = [
        sample["zram"]["memoryUsedTotalBytes"]
        for sample in samples
        if isinstance(sample.get("zram"), dict)
        and isinstance(sample["zram"].get("memoryUsedTotalBytes"), int)
    ]
    return {
        "sampleCount": len(samples),
        "minMemAvailableKb": min(mem) if mem else None,
        "swapFreeStartKb": swap[0] if swap else None,
        "minSwapFreeKb": min(swap) if swap else None,
        "swapFreeDropKb": swap[0] - min(swap) if swap else None,
        "maxProcessVmHwmKb": max(hwm) if hwm else None,
        "zramMemoryUsedStartBytes": zram[0] if zram else None,
        "zramMemoryUsedPeakBytes": max(zram) if zram else None,
        "zramGrowthBytes": max(zram) - zram[0] if zram else None,
    }


@dataclass
class ModeSession:
    mode: str
    pid: int
    host_port: int
    log_path: str
    command: str
    sampler: TelemetrySampler | None = None


def run_mode(
    adb: Adb,
    defaults: dict[str, Any],
    target: dict[str, Any],
    draft: dict[str, Any] | None,
    prompts: list[dict[str, Any]],
    mode: str,
    repetitions: int,
    stamp: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parts = build_server_parts(defaults, target, draft, mode)
    host_port = (
        int(defaults.get("dryRunHostPort", defaults["remotePort"] + 10000))
        if adb.dry_run
        else choose_host_port()
    )
    state_before_load = capture_state(adb, None)
    pid, log_path, command = start_server(adb, defaults, parts, mode, stamp)
    session = ModeSession(mode, pid, host_port, log_path, command)
    session_evidence: dict[str, Any] = {
        "mode": mode,
        "pid": pid if pid else None,
        "hostPort": host_port,
        "remotePort": defaults["remotePort"],
        "serverCommand": command,
        "serverLogPath": log_path,
        "startedAt": now_utc(),
        "stateBeforeLoad": state_before_load,
        "ready": None,
    }
    runs: list[dict[str, Any]] = []

    if adb.dry_run:
        adb.run(["forward", f"tcp:{host_port}", f"tcp:{defaults['remotePort']}"])
        for repetition in range(1, repetitions + 1):
            for prompt in prompts:
                runs.append(
                    {
                        "mode": mode,
                        "promptId": prompt["id"],
                        "category": prompt["category"],
                        "repetition": repetition,
                        "ok": None,
                        "dryRun": True,
                        "request": request_payload(prompt, defaults, repetition),
                    }
                )
        session_evidence["dryRun"] = True
        return session_evidence, runs

    try:
        adb.run(
            ["forward", f"tcp:{host_port}", f"tcp:{defaults['remotePort']}"],
            timeout=30,
        )

        def alive() -> bool:
            output = adb.shell(
                f"kill -0 {pid} 2>/dev/null; echo $?",
                timeout=10,
                check=False,
                show_dry_run=False,
            )
            return output.splitlines()[-1:] == ["0"]

        session.sampler = TelemetrySampler(
            adb, pid, defaults["telemetryIntervalSeconds"]
        )
        session.sampler.start()
        ready = wait_for_health(host_port, defaults["startupTimeoutSeconds"], alive)
        session_evidence["ready"] = True
        session_evidence["health"] = ready
        session_evidence["stateAfterLoad"] = capture_state(adb, pid)

        endpoint = f"http://127.0.0.1:{host_port}/v1/chat/completions"
        for repetition in range(1, repetitions + 1):
            for prompt in prompts:
                before = capture_state(adb, pid)
                payload = request_payload(prompt, defaults, repetition)
                run: dict[str, Any] = {
                    "mode": mode,
                    "promptId": prompt["id"],
                    "category": prompt["category"],
                    "repetition": repetition,
                    "startedAt": now_utc(),
                    "request": payload,
                    "stateBefore": before,
                }
                try:
                    response = stream_completion(
                        endpoint,
                        payload,
                        defaults["requestTimeoutSeconds"],
                    )
                    run.update(response)
                    run["ok"] = True
                except Exception as exc:
                    run["ok"] = False
                    run["error"] = str(exc)
                run["stateAfter"] = capture_state(adb, pid)
                run["finishedAt"] = now_utc()
                runs.append(run)
                if not run["ok"]:
                    raise HarnessError(
                        f"{mode} {prompt['id']} repetition {repetition} failed: "
                        f"{run['error']}"
                    )
            if defaults.get("cooldownSeconds", 0) > 0 and repetition < repetitions:
                time.sleep(defaults["cooldownSeconds"])
    except Exception as exc:
        session_evidence["error"] = str(exc)
        session_evidence["ready"] = session_evidence.get("ready") or False
    finally:
        if session.sampler is not None:
            session.sampler.stop()
        session_evidence["stateBeforeStop"] = capture_state(adb, pid)
        if session.sampler is not None:
            telemetry = [
                session_evidence["stateBeforeLoad"],
                *session.sampler.samples,
                session_evidence["stateBeforeStop"],
            ]
            session_evidence["telemetry"] = telemetry
            session_evidence["memorySummary"] = memory_summary(telemetry)
        session_evidence["serverLogTail"] = tail_log(adb, log_path)
        stop_server(adb, pid)
        adb.run(
            ["forward", "--remove", f"tcp:{host_port}"],
            timeout=30,
            check=False,
            show_dry_run=False,
        )
        session_evidence["finishedAt"] = now_utc()
    return session_evidence, runs


def compare(
    runs: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    speculative_mode = defaults.get("speculativeMode", "mtp")
    modes = ("baseline", speculative_mode)
    summaries = {mode: summarize_runs(runs, mode) for mode in modes}
    baseline_speed = summaries["baseline"]["predictedTokensPerSecond"]["mean"]
    speculative_speed = summaries[speculative_mode]["predictedTokensPerSecond"]["mean"]
    speedup = (
        speculative_speed / baseline_speed
        if isinstance(speculative_speed, (int, float))
        and isinstance(baseline_speed, (int, float))
        and baseline_speed > 0
        else None
    )
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for run in runs:
        if run.get("ok") is not True:
            continue
        key = (run["promptId"], run["repetition"])
        by_key.setdefault(key, {})[run["mode"]] = run
    pairs = []
    for (prompt_id, repetition), pair in sorted(by_key.items()):
        if "baseline" not in pair or speculative_mode not in pair:
            continue
        matched = (
            pair["baseline"].get("content")
            == pair[speculative_mode].get("content")
        )
        pairs.append(
            {
                "promptId": prompt_id,
                "repetition": repetition,
                "outputMatches": matched,
                "baselineSha256": pair["baseline"].get("contentSha256"),
                f"{speculative_mode}Sha256": pair[speculative_mode].get(
                    "contentSha256"
                ),
            }
        )
    output_matches = bool(pairs) and all(pair["outputMatches"] for pair in pairs)
    expected_pairs = len({run.get("promptId") for run in runs}) * int(
        defaults["repetitions"]
    )
    baseline_session = next(
        (session for session in sessions if session.get("mode") == "baseline"), {}
    )
    speculative_session = next(
        (
            session
            for session in sessions
            if session.get("mode") == speculative_mode
        ),
        {},
    )
    baseline_memory = baseline_session.get("memorySummary", {})
    memory = speculative_session.get("memorySummary", {})
    zram_growth = memory.get("zramGrowthBytes")
    max_zram_growth = int(defaults.get("maxZramGrowthBytes", 0))
    no_zram_growth = (
        zram_growth <= max_zram_growth
        if isinstance(zram_growth, int)
        else True if defaults.get("requireZramMetric") is False else None
    )
    swap_free_drop = memory.get("swapFreeDropKb")
    max_swap_free_drop = int(defaults.get("maxSwapFreeDropKb", 0))
    no_swap_use = (
        swap_free_drop <= max_swap_free_drop
        if isinstance(swap_free_drop, int)
        else None
    )
    baseline_rss = baseline_memory.get("maxProcessVmHwmKb")
    speculative_rss = memory.get("maxProcessVmHwmKb")
    rss_increase = (
        speculative_rss - baseline_rss
        if isinstance(speculative_rss, int) and isinstance(baseline_rss, int)
        else None
    )

    def thermal_summary(mode: str, session: dict[str, Any]) -> dict[str, Any]:
        states: list[dict[str, Any]] = []
        for key in ("stateBeforeLoad", "stateAfterLoad", "stateBeforeStop"):
            if isinstance(session.get(key), dict):
                states.append(session[key])
        for run in runs:
            if run.get("mode") != mode:
                continue
            for key in ("stateBefore", "stateAfter"):
                if isinstance(run.get(key), dict):
                    states.append(run[key])
        temperatures = [
            float(state["batteryTempC"])
            for state in states
            if isinstance(state.get("batteryTempC"), (int, float))
        ]
        statuses = [
            state["thermalStatus"]
            for state in states
            if isinstance(state.get("thermalStatus"), str)
        ]
        start = temperatures[0] if temperatures else None
        peak = max(temperatures) if temperatures else None
        return {
            "startBatteryTempC": start,
            "peakBatteryTempC": peak,
            "batteryTempRiseC": (
                peak - start
                if isinstance(peak, float) and isinstance(start, float)
                else None
            ),
            "thermalStatuses": list(dict.fromkeys(statuses)),
        }

    thermals = {
        mode: thermal_summary(
            mode,
            baseline_session if mode == "baseline" else speculative_session,
        )
        for mode in modes
    }
    gates = {
        "bothModesLoaded": all(
            session.get("ready") is True for session in sessions
        )
        and len(sessions) == 2,
        "allRunsSucceeded": bool(runs) and all(run.get("ok") is True for run in runs),
        "allOutputsNonEmpty": bool(runs)
        and all(bool(run.get("content")) for run in runs),
        "allPairsPresent": len(pairs) == expected_pairs,
        "outputsMatch": output_matches,
        "speculationActive": summaries[speculative_mode]["draftTokens"] > 0,
        "noZramGrowth": no_zram_growth,
        "noSwapUse": no_swap_use,
        "minimumSpeedupMet": (
            speedup >= float(defaults.get("minimumSpeedup", 1.0))
            if speedup is not None
            else None
        ),
    }
    if "maxSpeculativeRssIncreaseKb" in defaults:
        gates["rssIncreaseWithinLimit"] = (
            rss_increase <= int(defaults["maxSpeculativeRssIncreaseKb"])
            if isinstance(rss_increase, int)
            else None
        )
    if "maxBatteryTempC" in defaults:
        peak = thermals[speculative_mode]["peakBatteryTempC"]
        gates["batteryTempWithinLimit"] = (
            peak <= float(defaults["maxBatteryTempC"])
            if isinstance(peak, (int, float))
            else None
        )
    if "maxBatteryTempRiseC" in defaults:
        rise = thermals[speculative_mode]["batteryTempRiseC"]
        gates["batteryTempRiseWithinLimit"] = (
            rise <= float(defaults["maxBatteryTempRiseC"])
            if isinstance(rise, (int, float))
            else None
        )
    if any(value is False for value in gates.values()):
        verdict = "fail"
    elif all(value is True for value in gates.values()):
        verdict = "pass"
    else:
        verdict = "inconclusive"
    return {
        "modes": summaries,
        "decodeSpeedup": speedup,
        "pairedOutputs": pairs,
        "resources": {
            "baseline": {
                "memory": baseline_memory,
                "thermal": thermals["baseline"],
            },
            speculative_mode: {
                "memory": memory,
                "thermal": thermals[speculative_mode],
                "rssIncreaseVsBaselineKb": rss_increase,
            },
        },
        "gates": gates,
        "verdict": verdict,
    }


def build_dry_run_evidence(
    suite: dict[str, Any],
    defaults: dict[str, Any],
    target: dict[str, Any],
    draft: dict[str, Any] | None,
    adb: Adb,
    repetitions: int,
    only_mode: str | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    modes = [only_mode] if only_mode else list(defaults["modeOrder"])
    sessions: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for mode in modes:
        session, mode_runs = run_mode(
            adb,
            defaults,
            target,
            draft,
            suite["prompts"],
            mode,
            repetitions,
            stamp,
        )
        sessions.append(session)
        runs.extend(mode_runs)
    return {
        "schemaVersion": 1,
        "suite": suite["suite"],
        "timestampUtc": now_utc(),
        "dryRun": True,
        "provenance": provenance,
        "sessions": sessions,
        "runs": runs,
        "comparison": {"verdict": "inconclusive", "reason": "dry run"},
    }


def main(default_suite: Path = DEFAULT_SUITE) -> int:
    parser = argparse.ArgumentParser(
        description="Run same-target speculative off/on generation over adb."
    )
    parser.add_argument("suite", nargs="?", default=str(default_suite))
    parser.add_argument("--serial")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--only-mode")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--cooldown", type=int)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    try:
        suite_path = Path(args.suite)
        suite = load_json(suite_path)
        manifest, model_map = load_manifest()
        defaults = validate_suite(suite, model_map)
        if args.repetitions is not None:
            if args.repetitions < 1:
                raise HarnessError("--repetitions must be >= 1")
            defaults["repetitions"] = args.repetitions
        if args.cooldown is not None:
            if args.cooldown < 0:
                raise HarnessError("--cooldown must be >= 0")
            defaults["cooldownSeconds"] = args.cooldown
        target = model_map[defaults["targetModel"]]
        draft = (
            model_map[defaults["draftModel"]]
            if defaults["speculativeMode"] == "mtp"
            else None
        )
        patch_path = (
            REPO_ROOT / defaults["candidatePatch"]
            if defaults.get("candidatePatch")
            else None
        )
        if patch_path is not None and not patch_path.is_file():
            raise HarnessError(f"candidate patch not found: {patch_path}")
        suite_display = (
            str(suite_path.resolve().relative_to(REPO_ROOT))
            if suite_path.resolve().is_relative_to(REPO_ROOT)
            else str(suite_path.resolve())
        )
        provenance = {
            "runnerFile": "tools/run_spec_ab.py",
            "runnerFileSha256": sha256_file(Path(__file__).resolve()),
            "suiteFile": suite_display,
            "suiteFileSha256": sha256_file(suite_path),
            "manifestFile": "models/manifest.json",
            "manifestFileSha256": sha256_file(REPO_ROOT / "models" / "manifest.json"),
            "promptCanonicalization": (
                "UTF-8 JSON of messages with sorted keys and compact separators"
            ),
            "prompts": prompt_provenance(suite["prompts"]),
        }
        if patch_path is not None:
            provenance["candidatePatchFile"] = defaults["candidatePatch"]
            provenance["candidatePatchFileSha256"] = sha256_file(patch_path)
        source_state = llama_cpp_source_state()
        if patch_path is not None:
            source_state["candidatePatchApplied"] = candidate_patch_applied(
                patch_path
            )
        if source_state["commit"] != defaults["llamaCppBaseCommit"]:
            raise HarnessError(
                "pinned llama.cpp commit does not match suite: "
                f"checkout={source_state['commit']}, "
                f"suite={defaults['llamaCppBaseCommit']}"
            )
        if patch_path is not None and not source_state["candidatePatchApplied"]:
            raise HarnessError(
                "candidate llama.cpp patch is not applied; run "
                "python tools/apply_llama_patch.py --apply"
            )
        print(
            f"validated {suite['suite']}: target={target['id']}, "
            f"mode={defaults['speculativeMode']}, "
            f"draft={draft['id'] if draft else 'none'}, "
            f"prompts={len(suite['prompts'])}, "
            f"repetitions={defaults['repetitions']}"
        )
        if args.validate_only:
            return 0

        serial = resolve_serial(args.serial, args.dry_run)
        adb = Adb(serial, args.dry_run)
        identity = device_identity(adb)
        checked = preflight(adb, defaults, target, draft)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        modes = [args.only_mode] if args.only_mode else list(defaults["modeOrder"])
        invalid_modes = set(modes) - {
            "baseline",
            defaults["speculativeMode"],
        }
        if invalid_modes:
            raise HarnessError(
                f"invalid --only-mode for this suite: {sorted(invalid_modes)}"
            )

        if args.dry_run:
            evidence = build_dry_run_evidence(
                suite,
                defaults,
                target,
                draft,
                adb,
                defaults["repetitions"],
                args.only_mode,
                provenance,
            )
        else:
            sessions: list[dict[str, Any]] = []
            runs: list[dict[str, Any]] = []
            for index, mode in enumerate(modes):
                print(f"[{mode}] launching llama-server")
                session, mode_runs = run_mode(
                    adb,
                    defaults,
                    target,
                    draft,
                    suite["prompts"],
                    mode,
                    defaults["repetitions"],
                    stamp,
                )
                sessions.append(session)
                runs.extend(mode_runs)
                print(
                    f"[{mode}] ready={session.get('ready')} "
                    f"runs={sum(run.get('ok') is True for run in mode_runs)}/"
                    f"{len(mode_runs)}"
                )
                if (
                    defaults.get("modeCooldownSeconds", 0) > 0
                    and index < len(modes) - 1
                ):
                    time.sleep(defaults["modeCooldownSeconds"])
            comparison = (
                compare(runs, sessions, defaults)
                if len(modes) == 2
                else {
                    "verdict": "inconclusive",
                    "reason": "both modes are required for comparison",
                    "modes": {
                        mode: summarize_runs(runs, mode) for mode in modes
                    },
                }
            )
            evidence = {
                "schemaVersion": 1,
                "suite": suite["suite"],
                "description": suite.get("description"),
                "timestampUtc": now_utc(),
                "dryRun": False,
                "provenance": provenance,
                "device": identity,
                "software": {
                    "appCommit": app_commit(),
                    "llamaCppSource": source_state,
                    "binary": defaults["binary"],
                },
                "models": {
                    "target": target,
                    **({"draft": draft} if draft else {}),
                },
                "configuration": defaults,
                "preflight": checked,
                "sessions": sessions,
                "runs": runs,
                "comparison": comparison,
            }

        evidence.setdefault("device", identity)
        evidence.setdefault(
            "software",
            {
                "appCommit": app_commit(),
                "llamaCppSource": source_state,
                "binary": defaults["binary"],
            },
        )
        evidence.setdefault(
            "models",
            {"target": target, **({"draft": draft} if draft else {})},
        )
        evidence.setdefault("configuration", defaults)
        evidence.setdefault("preflight", checked)
        out_dir = Path(args.out_dir) / f"{stamp}-{suite['suite']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / defaults["artifactFile"]
        out_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path}")

        if args.dry_run:
            return 0
        if any(session.get("ready") is not True for session in evidence["sessions"]):
            return 2
        if any(run.get("ok") is not True for run in evidence["runs"]):
            return 2
        return 0
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
