#!/usr/bin/env python3
"""Run a llama-bench suite over adb; write one schemaVersion-1 result JSON per case.

Usage:
  python tools/run_suite.py benchmarks/suites/phase1-baseline.json [options]

Options:
  --serial SERIAL       adb device serial (default: first attached device)
  --dry-run             print the adb commands without running them (needs no device)
  --only-backend B      run only cases with this backend (repeatable, e.g. --only-backend cpu)
  --cooldown SECONDS    idle seconds between cases to shed heat (default: 120)
  --out-dir DIR         base results dir (default: benchmarks/results/raw)
  --device-snapshot F   JSON from device_snapshot.py, imported to fill the device block
  --ndk-version V       recorded in the software block (else null)

stdlib only, Python 3.12, Windows + Linux. adb must be on PATH.
"""
import argparse
import datetime
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_INTERRUPTED = False

# android.os.PowerManager thermal status codes, as reported by `dumpsys thermalservice`
THERMAL_STATUS = {0: "NONE", 1: "LIGHT", 2: "MODERATE", 3: "SEVERE",
                  4: "CRITICAL", 5: "EMERGENCY", 6: "SHUTDOWN"}


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def adb_base(serial: str | None) -> list[str]:
    return ["adb"] + (["-s", serial] if serial else [])


def run_adb(serial: str | None, args: list[str], dry_run: bool, timeout: int | None = 60) -> str:
    """Run `adb <args>`; in dry-run just print and return "". Returns stdout."""
    cmd = adb_base(serial) + args
    if dry_run:
        print("  $ " + " ".join(cmd))
        return ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        sys.exit("adb not found on PATH")
    if r.returncode != 0 and r.stderr.strip():
        print(f"  ! adb stderr: {r.stderr.strip()}", file=sys.stderr)
    return r.stdout


def first_serial(dry_run: bool) -> str | None:
    if dry_run:
        return None
    out = run_adb(None, ["devices"], dry_run=False)
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    if not devs:
        sys.exit("no device connected (use --dry-run to preview commands)")
    return devs[0]


# ---- device state ----------------------------------------------------------

def load_model_map() -> dict:
    """id -> manifest entry, from models/manifest.json."""
    manifest = json.loads((REPO_ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))
    return {m["id"]: m for m in manifest.get("models", [])}


def app_commit() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


BATT_CMD = "dumpsys battery | grep -E 'level|temperature|status'"
# `-i status` also matches "IsStatusOverride:", which sorts first — match the real line.
THERMAL_CMD = "dumpsys thermalservice | grep -m1 'Thermal Status'"
MEM_CMD = "grep MemAvailable /proc/meminfo"


def capture_thermal(serial: str | None, dry_run: bool) -> dict:
    """Compact thermal/battery/memory reading via dumpsys + procfs (read-only)."""
    if dry_run:
        for cmd in (BATT_CMD, THERMAL_CMD, MEM_CMD):
            run_adb(serial, ["shell", cmd], dry_run=True)
        return {"batteryLevel": None, "batteryTempC": None, "thermalStatus": None,
                "memAvailableKb": None, "capturedAt": now_utc()}
    batt = run_adb(serial, ["shell", BATT_CMD], dry_run=False)
    status = run_adb(serial, ["shell", THERMAL_CMD], dry_run=False)
    mem = run_adb(serial, ["shell", MEM_CMD], dry_run=False)
    level = _grep_int(batt, r"level:\s*(\d+)")
    temp_raw = _grep_int(batt, r"temperature:\s*(\d+)")  # tenths of degC
    code = _grep_int(status, r"Thermal Status:\s*(\d+)")
    return {
        "batteryLevel": level,
        "batteryTempC": (temp_raw / 10.0) if temp_raw is not None else None,
        "thermalStatus": (f"{code} ({THERMAL_STATUS.get(code, '?')})" if code is not None
                          else (status.strip() or None)),
        "memAvailableKb": _grep_int(mem, r"MemAvailable:\s*(\d+)"),
        "capturedAt": now_utc(),
    }


def _grep_int(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def device_block(serial: str | None, snapshot: dict | None, dry_run: bool) -> dict:
    """Static device identity; thermal start/end filled by the caller per run."""
    if snapshot:
        return {
            "model": snapshot.get("model"),
            "android": snapshot.get("android"),
            "soc": snapshot.get("soc"),
            "vulkanDevice": snapshot.get("vulkanDevice"),
            "vulkanDriver": snapshot.get("vulkanDriver"),
            "serial": snapshot.get("serial") or serial,
        }
    if dry_run:
        for prop in ("ro.product.model", "ro.build.version.release", "ro.soc.model"):
            run_adb(serial, ["shell", "getprop", prop], dry_run=True)
        return {"model": None, "android": None, "soc": None,
                "vulkanDevice": None, "vulkanDriver": None, "serial": serial}
    gp = lambda p: run_adb(serial, ["shell", "getprop", p], dry_run=False).strip() or None
    return {
        "model": gp("ro.product.model"),
        "android": gp("ro.build.version.release"),
        "soc": gp("ro.soc.model"),
        "vulkanDevice": None,   # not queryable via a fast adb call; fill via --device-snapshot
        "vulkanDriver": None,
        "serial": serial,
    }


# ---- bench command ---------------------------------------------------------

def build_bench_cmd(case: dict, defaults: dict, model_file: str) -> tuple[list[str], str]:
    """Return (adb_args, human_shell_string) for one case."""
    bin_dir = case.get("binDir", defaults["binDir"])
    binary = case.get("binary", defaults.get("binary", "llama-bench"))
    models_dir = defaults["modelsDir"]
    reps = case.get("repetitions", defaults.get("repetitions", 5))
    t = case["test"]
    parts = [
        f"./{binary}",
        "-m", f"{models_dir}/{model_file}",
        "-t", str(case["threads"]),
        "-ngl", str(case["nGpuLayers"]),
        "-r", str(reps),
    ]
    if t["type"] == "pp":
        parts += ["-p", str(t["promptTokens"]), "-n", "0"]
    elif t["type"] == "tg":
        parts += ["-p", "0", "-n", str(t["genTokens"])]
    elif t["type"] == "pg":
        parts += ["-p", "0", "-n", "0", "-pg", f"{t['promptTokens']},{t['genTokens']}"]
    else:
        raise ValueError(f"unknown test type: {t['type']}")
    if case.get("cpuMask"):
        parts += ["-C", case["cpuMask"]]
    if defaults.get("warmup") is False:
        parts.append("--no-warmup")
    parts += ["-o", "json"]
    shell = f"cd {bin_dir} && LD_LIBRARY_PATH={bin_dir} " + " ".join(parts)
    return ["shell", shell], shell


# ---- metrics ---------------------------------------------------------------

def metrics_from_bench(bench: list[dict]) -> dict:
    """Map llama-bench JSON entries to pp/tg/pg tokens-per-second mean/std."""
    out: dict = {}
    for e in bench:
        np_, ng = e.get("n_prompt", 0), e.get("n_gen", 0)
        stat = {"mean": e.get("avg_ts"), "std": e.get("stddev_ts"),
                "samples": e.get("samples_ts")}
        if np_ and not ng:
            out["ppTokensPerSec"] = stat
        elif ng and not np_:
            out["tgTokensPerSec"] = stat
        elif np_ and ng:
            out["pgTokensPerSec"] = stat
    return out


# ---- main ------------------------------------------------------------------

def run_case(case, defaults, model_entry, serial, dry_run, ctx) -> dict:
    model_file = model_entry["file"] if model_entry else "MODEL_NOT_IN_MANIFEST.gguf"
    adb_args, shell = build_bench_cmd(case, defaults, model_file)

    thermal_start = capture_thermal(serial, dry_run)
    print(f"[{case['id']}] {case['backend']} t={case['threads']} ngl={case['nGpuLayers']}")
    out = run_adb(serial, adb_args, dry_run, timeout=1800)
    thermal_end = capture_thermal(serial, dry_run)

    bench, raw_text, parse_error = [], out, None
    if not dry_run:
        try:
            bench = json.loads(out)
        except json.JSONDecodeError as ex:
            parse_error = f"{ex}"

    t = case["test"]
    result = {
        "schemaVersion": 1,
        "suite": ctx["suite"],
        "caseId": case["id"],
        "timestampUtc": now_utc(),
        "dryRun": dry_run,
        "device": {
            **ctx["device"],
            "thermalStart": thermal_start,
            "thermalEnd": thermal_end,
        },
        "software": {
            "appCommit": ctx["appCommit"],
            "llamaCppCommit": (bench[0].get("build_commit") if bench else None),
            "buildVariant": case["backend"],
            "ndkVersion": ctx["ndkVersion"],
        },
        "model": {
            "name": (model_entry or {}).get("id"),
            "sha256": (model_entry or {}).get("sha256"),
            "quantization": (model_entry or {}).get("quantization"),
            "contextSize": defaults.get("contextSize"),
        },
        "run": {
            "backend": case["backend"],
            "threads": case["threads"],
            "nGpuLayers": case["nGpuLayers"],
            "promptTokens": t.get("promptTokens", 0),
            "generatedTokens": t.get("genTokens", 0),
            "seed": case.get("seed", defaults.get("seed")),
            "repetition": case.get("repetitions", defaults.get("repetitions", 5)),
        },
        "metrics": metrics_from_bench(bench),
        "rawBench": bench,
    }
    if parse_error:
        result["benchParseError"] = parse_error
        result["rawBenchText"] = raw_text
    return result


def install_sigint():
    def handler(signum, frame):
        global _INTERRUPTED
        _INTERRUPTED = True
        print("\n^C received — finishing current write, then stopping.", file=sys.stderr)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a llama-bench suite over adb.")
    ap.add_argument("suite", help="path to a suite JSON (benchmarks/suites/*.json)")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-backend", action="append", default=None, metavar="BACKEND",
                    help="run only cases with this backend (repeatable; e.g. --only-backend cpu)")
    ap.add_argument("--cooldown", type=int, default=120, help="idle seconds between cases")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "benchmarks" / "results" / "raw"))
    ap.add_argument("--device-snapshot", default=None)
    ap.add_argument("--ndk-version", default=None)
    args = ap.parse_args()

    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    defaults = suite.get("defaults", {})
    suite_name = suite.get("suite", Path(args.suite).stem)
    cases = suite["cases"]

    if args.only_backend:
        wanted = {b.lower() for b in args.only_backend}
        kept = [c for c in cases if c.get("backend", "").lower() in wanted]
        skipped = len(cases) - len(kept)
        if not kept:
            sys.exit(f"no cases with backend in {sorted(wanted)} (suite has "
                     f"{sorted({c.get('backend') for c in cases})})")
        print(f"--only-backend {sorted(wanted)}: {len(kept)} case(s) kept, {skipped} skipped")
        cases = kept

    serial = args.serial or first_serial(args.dry_run)
    snapshot = json.loads(Path(args.device_snapshot).read_text(encoding="utf-8")) if args.device_snapshot else None
    model_map = load_model_map()

    ctx = {
        "suite": suite_name,
        "appCommit": app_commit(),
        "ndkVersion": args.ndk_version,
        "device": device_block(serial, snapshot, args.dry_run),
    }

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / f"{stamp}-{suite_name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"suite '{suite_name}': {len(cases)} case(s) -> {out_dir}"
          + (" [DRY RUN]" if args.dry_run else ""))

    install_sigint()
    written = 0
    for i, case in enumerate(cases):
        if _INTERRUPTED:
            break
        model_id = case.get("model", defaults.get("model"))
        model_entry = model_map.get(model_id)
        if model_entry is None:
            print(f"  ! model id '{model_id}' not in manifest — recording placeholder", file=sys.stderr)
        result = run_case(case, defaults, model_entry, serial, args.dry_run, ctx)
        path = out_dir / f"{case['id']}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        written += 1
        print(f"  wrote {path.name}")

        last = i == len(cases) - 1
        if not last and not args.dry_run and not _INTERRUPTED and args.cooldown > 0:
            print(f"  cooldown {args.cooldown}s...")
            try:
                time.sleep(args.cooldown)
            except KeyboardInterrupt:
                break

    print(f"\ndone: {written}/{len(cases)} case(s) written to {out_dir}")
    if args.dry_run:
        print("dry run — no device commands executed, results are placeholders.")


if __name__ == "__main__":
    main()
