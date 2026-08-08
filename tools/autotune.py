#!/usr/bin/env python3
"""Portable big.LITTLE core-policy autotuner for llama.cpp on Arm/Android.

Ship the method, not the constant (docs/PLAN.md §1 rule 6). On the measured Arm
big.LITTLE device, prefill and decode prefer different thread widths and the
answer changed across builds. The supported mechanism combines a DRAM-bound
decode phase with synchronization/spin-wait overhead; it is not a universal
A55-versus-A78 rule. This tool therefore measures the answer instead of encoding
a remembered constant.

What it does:
  1. Reads CPU topology over adb (read-only): `/proc/cpuinfo` MIDR part IDs,
     `/sys/.../cpufreq/cpuinfo_max_freq` per core, `/proc/meminfo`. Cores are
     clustered by max frequency (the portable big/little signal); MIDR is the
     cross-check and the human-readable label. Unknown MIDRs degrade gracefully.
  2. Derives a candidate list from that topology (never from hardcoded numbers):
     cluster-prefix affinity sets, homogeneous thread policies, and phase pairs
     with a wide prefill (`n_threads_batch`) plus topology-derived decode width.
  3. Runs a short, *counterbalanced*, *thermally gated* llama-bench sweep.
  4. Picks a winner with a variance-penalising rule (see `score_rows`).
  5. Caches the winning profile by device plus binary/model/workload identity.
  6. Consults a bundled known-good profile table first so most users never sweep.

Measurement hygiene (this is most of the code, on purpose):
  * The stock default (no `-t`, no `-C`) is always candidate #0 and is the
    baseline every ratio is expressed against — the headline claim is
    "autotuned beats stock defaults", so stock must be measured, not assumed.
  * One discarded warm-up invocation precedes the sweep. A cold page cache
    measured 1.11 tok/s vs 10.50 warm on the test device; that run is recorded
    with `"scored": false` and never enters a mean.
  * Candidates are run in **rounds**, and the order is reversed on alternate
    rounds (ABBA counterbalancing). Running all reps of A then all reps of B lets
    monotonic drift load entirely onto whichever ran last; measured on the test
    device, back-to-back stock-then-t6 with no cooldown produced an apparent 2.5x
    difference (pp512 76.30 -> 30.70) that was pure thermal throttling.
  * Between candidates the tool idles `--cooldown` seconds and then *polls*
    battery temperature until the device is back at or below the sweep's own
    start temperature (+slack), or `--thermal-max-wait` elapses.
  * Thermal state is recorded before and after every candidate. If temperature
    rose beyond `--max-temp-rise-c`, or a cooldown gate timed out, the sweep is
    flagged and **no winner is crowned** — the verdict is `inconclusive` and the
    recommendation falls back to stock defaults.
  * `android.os.PowerManager` thermal status is recorded and warned about but
    does **not** gate by default; MODERATE is the resting state on a
    USB-charging device and gating on it would make every sweep inconclusive.
    See THERMAL_STATUS_ADVISORY. Opt in with `--max-thermal-status N`.

Usage:
  python tools/autotune.py --dry-run
  python tools/autotune.py --serial SERIAL
  python tools/autotune.py --force --rounds 3 --cooldown 120

stdlib only, Python 3.12, Windows + Linux. adb must be on PATH. Every device
access is a read; nothing is pushed, written, or configured on the device.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# run_suite.py owns the adb plumbing, thermal/battery capture and the
# llama-bench JSON -> metrics mapping. Load it by path (tools/ is not a package),
# the same way tools/test_run_spec_ab.py loads its module under test.
_RS_SPEC = importlib.util.spec_from_file_location(
    "run_suite", Path(__file__).with_name("run_suite.py")
)
assert _RS_SPEC and _RS_SPEC.loader
run_suite = importlib.util.module_from_spec(_RS_SPEC)
sys.modules["run_suite"] = run_suite
_RS_SPEC.loader.exec_module(run_suite)

_POLICY_SPEC = importlib.util.spec_from_file_location(
    "export_android_policy", Path(__file__).with_name("export_android_policy.py")
)
assert _POLICY_SPEC and _POLICY_SPEC.loader
export_android_policy = importlib.util.module_from_spec(_POLICY_SPEC)
sys.modules[_POLICY_SPEC.name] = export_android_policy
_POLICY_SPEC.loader.exec_module(export_android_policy)

now_utc = run_suite.now_utc
run_adb = run_suite.run_adb
adb_base = run_suite.adb_base

DEFAULT_SUITE = REPO_ROOT / "benchmarks" / "suites" / "autotune.json"
DEFAULT_PROFILES = REPO_ROOT / "benchmarks" / "profiles" / "known-profiles.json"
DEFAULT_CACHE = REPO_ROOT / "benchmarks" / "results" / "autotune-cache.json"
DEFAULT_OUT_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"


class AutotuneError(RuntimeError):
    """A validation or measurement failure worth surfacing to the caller."""


# ---------------------------------------------------------------------------
# MIDR lookup. A convenience label only — clustering is driven by cpufreq, so an
# unknown part ID costs a nice name and nothing else. Keyed by
# (CPU implementer, CPU part) as printed by /proc/cpuinfo.
# ---------------------------------------------------------------------------
MIDR_PARTS: dict[tuple[str, str], str] = {
    ("0x41", "0xd03"): "Cortex-A53",
    ("0x41", "0xd04"): "Cortex-A35",
    ("0x41", "0xd05"): "Cortex-A55",
    ("0x41", "0xd07"): "Cortex-A57",
    ("0x41", "0xd08"): "Cortex-A72",
    ("0x41", "0xd09"): "Cortex-A73",
    ("0x41", "0xd0a"): "Cortex-A75",
    ("0x41", "0xd0b"): "Cortex-A76",
    ("0x41", "0xd0c"): "Neoverse-N1",
    ("0x41", "0xd0d"): "Cortex-A77",
    ("0x41", "0xd0e"): "Cortex-A76AE",
    ("0x41", "0xd40"): "Neoverse-V1",
    ("0x41", "0xd41"): "Cortex-A78",
    ("0x41", "0xd42"): "Cortex-A78AE",
    ("0x41", "0xd44"): "Cortex-X1",
    ("0x41", "0xd46"): "Cortex-A510",
    ("0x41", "0xd47"): "Cortex-A710",
    ("0x41", "0xd48"): "Cortex-X2",
    ("0x41", "0xd49"): "Neoverse-N2",
    ("0x41", "0xd4d"): "Cortex-A715",
    ("0x41", "0xd4e"): "Cortex-X3",
    ("0x41", "0xd4f"): "Neoverse-V2",
    ("0x41", "0xd80"): "Cortex-A520",
    ("0x41", "0xd81"): "Cortex-A720",
    ("0x41", "0xd82"): "Cortex-X4",
    ("0x41", "0xd85"): "Cortex-X925",
    ("0x41", "0xd87"): "Cortex-A725",
}

# Only used when cpufreq is unavailable, to guess which cluster is the "big" one.
# Cores absent from this table rank 0 and the topology is marked low-confidence.
MIDR_PERF_RANK: dict[str, int] = {
    "0xd04": 1, "0xd03": 1, "0xd05": 1, "0xd46": 1, "0xd80": 1,   # efficiency
    "0xd07": 2, "0xd08": 2, "0xd09": 2, "0xd0a": 2, "0xd0b": 2,   # mid/perf
    "0xd0d": 2, "0xd41": 2, "0xd42": 2, "0xd47": 2, "0xd4d": 2, "0xd87": 2,
    "0xd44": 3, "0xd48": 3, "0xd4e": 3, "0xd82": 3, "0xd85": 3,   # prime
}

TIER_NAMES_BY_COUNT: dict[int, list[str]] = {
    1: ["all"],
    2: ["big", "little"],
    3: ["big", "mid", "little"],
}

# Single read-only round trip for everything the topology needs.
TOPOLOGY_CMD = (
    "cat /proc/cpuinfo; echo __CPUFREQ__; "
    "grep . /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq 2>/dev/null; "
    "echo __PRESENT__; cat /sys/devices/system/cpu/present 2>/dev/null; "
    "echo __MEMINFO__; cat /proc/meminfo"
)
TOPOLOGY_SECTIONS = ("cpuinfo", "cpufreq", "present", "meminfo")

# A 2xA78 + 6xA55 stand-in used only by --dry-run so the preview can print real
# bench commands without touching a device. Clearly labelled in the output.
DRY_RUN_TOPOLOGY = {
    "cpuinfo": "".join(
        f"processor\t: {cpu}\n"
        "CPU implementer\t: 0x41\n"
        f"CPU part\t: {'0xd41' if cpu >= 6 else '0xd05'}\n\n"
        for cpu in range(8)
    ),
    "cpufreq": "".join(
        f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq:"
        f"{2200000 if cpu >= 6 else 2000000}\n"
        for cpu in range(8)
    ),
    "present": "0-7\n",
    "meminfo": "MemTotal:        5860000 kB\nMemAvailable:    1840000 kB\nSwapFree:        2500000 kB\n",
}


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cluster:
    """One group of cores that share a max frequency and a MIDR part."""

    maxFreqKhz: int | None
    cpuIds: tuple[int, ...]
    midrPart: str | None
    midrImplementer: str | None
    label: str
    tier: str  # big | mid | little | all | unknown

    @property
    def size(self) -> int:
        return len(self.cpuIds)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["cpuIds"] = list(self.cpuIds)
        data["size"] = self.size
        data["cpuMask"] = hex_mask(self.cpuIds)
        return data


@dataclass(frozen=True)
class Topology:
    coreCount: int
    clusters: tuple[Cluster, ...]
    memory: dict[str, int | None] = field(default_factory=dict)
    freqSignal: bool = True
    midrSignal: bool = True
    confidence: str = "high"
    source: str = "device"
    notes: tuple[str, ...] = ()

    @property
    def bigClusters(self) -> tuple[Cluster, ...]:
        return tuple(c for c in self.clusters if c.tier in ("big", "all"))

    @property
    def littleClusters(self) -> tuple[Cluster, ...]:
        return tuple(c for c in self.clusters if c.tier == "little")

    def cpu_ids(self) -> tuple[int, ...]:
        return tuple(sorted(cpu for c in self.clusters for cpu in c.cpuIds))

    def midr_signature(self) -> str:
        """Stable, freq-independent shorthand, e.g. `2x0xd41+6x0xd05`."""
        return "+".join(f"{c.size}x{c.midrPart or 'unknown'}" for c in self.clusters)

    def cluster_signature(self) -> str:
        """Full shorthand including frequency, e.g. `2x0xd41@2200000+6x0xd05@2000000`."""
        return "+".join(
            f"{c.size}x{c.midrPart or 'unknown'}@{c.maxFreqKhz if c.maxFreqKhz else 'unknown'}"
            for c in self.clusters
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "coreCount": self.coreCount,
            "clusters": [c.to_json() for c in self.clusters],
            "bigCpuIds": [cpu for c in self.bigClusters for cpu in c.cpuIds],
            "bigCoreCount": sum(c.size for c in self.bigClusters),
            "littleCoreCount": sum(c.size for c in self.littleClusters),
            "midrSignature": self.midr_signature(),
            "clusterSignature": self.cluster_signature(),
            "memory": self.memory,
            "freqSignal": self.freqSignal,
            "midrSignal": self.midrSignal,
            "confidence": self.confidence,
            "source": self.source,
            "notes": list(self.notes),
        }


def hex_mask(cpu_ids) -> str:
    """Bare lowercase hex affinity mask, as Android toybox `taskset` and
    llama-bench `-C` expect (no `0x`). `taskset c0` = cpu6+cpu7."""
    value = 0
    for cpu in cpu_ids:
        if cpu < 0:
            raise AutotuneError(f"negative cpu id: {cpu}")
        value |= 1 << cpu
    return format(value, "x")


def parse_cpuinfo(text: str) -> dict[int, dict[str, str]]:
    """processor id -> {'part': '0xd41', 'implementer': '0x41'} from /proc/cpuinfo."""
    result: dict[int, dict[str, str]] = {}
    current: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "processor":
            try:
                current = int(value)
            except ValueError:
                current = None
                continue
            result.setdefault(current, {})
        elif current is None:
            continue
        elif key == "cpu part":
            result[current]["part"] = value.lower()
        elif key == "cpu implementer":
            result[current]["implementer"] = value.lower()
    return result


def parse_cpufreq(text: str) -> dict[int, int]:
    """cpu id -> cpuinfo_max_freq (kHz).

    Accepts `grep .` output (`/sys/.../cpu3/cpufreq/cpuinfo_max_freq:2000000`)
    and plain `path value` pairs. Cores whose cpufreq node is missing (offline,
    or a kernel without cpufreq) are simply absent.
    """
    result: dict[int, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.search(r"/cpu(\d+)/", line)
        if not match:
            continue
        digits = re.findall(r"(\d+)\s*$", line)
        if not digits:
            continue
        try:
            result[int(match.group(1))] = int(digits[-1])
        except ValueError:
            continue
    return result


def parse_present(text: str) -> list[int]:
    """`0-7` / `0-3,6-7` -> explicit cpu id list."""
    cpus: set[int] = set()
    for chunk in text.strip().split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            low, _, high = chunk.partition("-")
            try:
                cpus.update(range(int(low), int(high) + 1))
            except ValueError:
                continue
        else:
            try:
                cpus.add(int(chunk))
            except ValueError:
                continue
    return sorted(cpus)


def parse_meminfo(text: str) -> dict[str, int | None]:
    def value(key: str) -> int | None:
        match = re.search(rf"^{key}:\s+(\d+)\s*kB", text, re.MULTILINE)
        return int(match.group(1)) if match else None

    return {
        "memTotalKb": value("MemTotal"),
        "memAvailableKb": value("MemAvailable"),
        "swapFreeKb": value("SwapFree"),
    }


def midr_label(implementer: str | None, part: str | None) -> str:
    if part is None:
        return "unknown core"
    known = MIDR_PARTS.get((implementer or "0x41", part))
    if known:
        return known
    return f"unknown core (implementer {implementer or '?'}, part {part})"


def build_topology(
    cpu_parts: dict[int, dict[str, str]],
    max_freqs: dict[int, int],
    present: list[int] | None = None,
    memory: dict[str, int | None] | None = None,
    source: str = "device",
) -> Topology:
    """Cluster cores by (max frequency, MIDR part).

    Frequency is the primary, portable signal; MIDR splits a frequency group that
    contains two different core types and supplies the label. Either signal alone
    still produces a usable topology; with neither, every core lands in one
    cluster and the topology is marked low-confidence so candidate generation
    stops proposing affinity masks it cannot justify.
    """
    cpus = sorted(set(present or []) | set(cpu_parts) | set(max_freqs))
    if not cpus:
        raise AutotuneError("no CPUs found in /proc/cpuinfo or /sys cpufreq")

    freq_signal = bool(max_freqs)
    midr_signal = any("part" in info for info in cpu_parts.values())
    notes: list[str] = []
    if not freq_signal:
        notes.append("no cpufreq data; clustering fell back to MIDR part IDs")
    if not midr_signal:
        notes.append("no MIDR part IDs; cluster labels are frequency-only")

    groups: dict[tuple[int | None, str | None], list[int]] = {}
    for cpu in cpus:
        info = cpu_parts.get(cpu, {})
        groups.setdefault((max_freqs.get(cpu), info.get("part")), []).append(cpu)

    def rank(key: tuple[int | None, str | None]) -> tuple[int, int, int]:
        freq, part = key
        # Sort strongest-first. Frequency wins when known; otherwise fall back to
        # a coarse MIDR performance class, and finally to cpu index order.
        return (
            freq if freq is not None else -1,
            MIDR_PERF_RANK.get(part or "", 0),
            -min(groups[key]),
        )

    ordered = sorted(groups, key=rank, reverse=True)
    tier_names = TIER_NAMES_BY_COUNT.get(len(ordered))
    if tier_names is None:
        tier_names = ["big"] + ["mid"] * (len(ordered) - 2) + ["little"]

    confidence = "high"
    if not freq_signal and not any(
        MIDR_PERF_RANK.get(part or "") for _, part in ordered
    ):
        confidence = "low"
        notes.append(
            "neither cpufreq nor a recognised MIDR part; big/little split is a guess"
        )
        tier_names = ["unknown"] * len(ordered)
    elif not freq_signal or not midr_signal:
        confidence = "medium"

    clusters = tuple(
        Cluster(
            maxFreqKhz=key[0],
            cpuIds=tuple(sorted(groups[key])),
            midrPart=key[1],
            midrImplementer=(cpu_parts.get(sorted(groups[key])[0], {}) or {}).get(
                "implementer"
            ),
            label=midr_label(
                (cpu_parts.get(sorted(groups[key])[0], {}) or {}).get("implementer"),
                key[1],
            ),
            tier=tier,
        )
        for key, tier in zip(ordered, tier_names)
    )
    return Topology(
        coreCount=len(cpus),
        clusters=clusters,
        memory=memory or {},
        freqSignal=freq_signal,
        midrSignal=midr_signal,
        confidence=confidence,
        source=source,
        notes=tuple(notes),
    )


def split_topology_output(text: str) -> dict[str, str]:
    """Split the single combined shell read into its four sections."""
    sections = {name: "" for name in TOPOLOGY_SECTIONS}
    cpuinfo, _, tail = text.partition("__CPUFREQ__")
    cpufreq, _, tail = tail.partition("__PRESENT__")
    present, _, meminfo = tail.partition("__MEMINFO__")
    sections["cpuinfo"] = cpuinfo
    sections["cpufreq"] = cpufreq
    sections["present"] = present
    sections["meminfo"] = meminfo
    return sections


def topology_from_sections(sections: dict[str, str], source: str) -> Topology:
    return build_topology(
        parse_cpuinfo(sections.get("cpuinfo", "")),
        parse_cpufreq(sections.get("cpufreq", "")),
        parse_present(sections.get("present", "")),
        parse_meminfo(sections.get("meminfo", "")),
        source=source,
    )


def read_topology(serial: str | None, dry_run: bool, topology_file: Path | None) -> Topology:
    if topology_file is not None:
        raw = json.loads(topology_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise AutotuneError(f"{topology_file} must be a JSON object")
        sections = {name: raw.get(name, "") for name in TOPOLOGY_SECTIONS}
        return topology_from_sections(sections, source=f"file:{topology_file.name}")
    output = run_adb(serial, ["shell", TOPOLOGY_CMD], dry_run, timeout=60)
    if dry_run:
        return topology_from_sections(DRY_RUN_TOPOLOGY, source="dry-run-placeholder")
    return topology_from_sections(split_topology_output(output), source="device")


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    id: str
    threads: int | None      # None = let llama.cpp choose (stock default)
    cpuMask: str | None      # bare hex mask for -C, or None for no pinning
    family: str              # stock | pinned | unpinned
    rationale: str
    threadsBatch: int | None = None  # None = inherit stock/default; llama-bench -tb

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def flags(self) -> str:
        parts = []
        if self.threads is not None:
            parts += ["-t", str(self.threads)]
        if self.threadsBatch is not None and self.threadsBatch != self.threads:
            parts += ["-tb", str(self.threadsBatch)]
        if self.cpuMask:
            parts += ["-C", self.cpuMask]
        return " ".join(parts) or "(stock defaults)"


STOCK_CANDIDATE = Candidate(
    id="stock-default",
    threads=None,
    cpuMask=None,
    family="stock",
    rationale=(
        "llama.cpp's own default thread count with no affinity mask. Every ratio "
        "in this report is measured against it, because the claim under test is "
        "'autotuned beats stock defaults'."
    ),
    threadsBatch=None,
)


def _boundary_thread_counts(sizes: list[int]) -> list[int]:
    """Thread counts worth probing without pinning, derived from cluster sizes.

    Cumulative cluster boundaries are the interesting points (that is where the
    scheduler can begin using another performance tier), plus the
    size of the little cluster on its own (Phase 2 measured six A55s matching the
    full SoC on decode), plus a midpoint whenever two boundaries are >= 3 apart
    so a wide little cluster is not skipped over.
    """
    total = sum(sizes)
    boundaries: set[int] = set()
    running = 0
    for size in sizes:
        running += size
        boundaries.add(running)
    if len(sizes) >= 2:
        boundaries.add(sizes[-1])          # little cluster width
        boundaries.add(total - sizes[-1])  # everything except the little cluster
    ordered = sorted(b for b in boundaries if b >= 1)
    filled = set(ordered)
    for low, high in zip(ordered, ordered[1:]):
        if high - low >= 3:
            filled.add((low + high) // 2)
    return sorted(t for t in filled if 1 <= t <= total)


def generate_candidates(topo: Topology, max_candidates: int | None = None) -> list[Candidate]:
    """Derive the sweep from the topology — no hardcoded thread counts.

    Four families:
      * `stock`    — the baseline, always first.
      * `pinned`   — one candidate per cluster-prefix set (big-only, big+mid, ...)
                     at full occupancy of that set, plus little-only. The full set
                     is omitted because pinning to every core is the unpinned case.
      * `unpinned` — explicit `-t` at each cluster boundary (and midpoints), with
                     no mask. This is where the non-monotonic scaling shows up.
      * `phase-pair` — the topology-wide thread count for prefill (`-tb`) paired
                       with each narrower decode width (`-t`). Affinity is omitted
                       because a single common mask cannot express two phases.

    A 2+6 chip yields t in {2,4,6,8} unpinned plus big-only/little-only masks;
    a 4+4 chip yields {4,6,8}; a 1+3+4 chip yields {1,2,4,6,8} plus
    big-only/big+mid/little-only. Nothing in here knows what a Dimensity is.
    """
    candidates: list[Candidate] = [STOCK_CANDIDATE]
    clusters = list(topo.clusters)
    sizes = [c.size for c in clusters]
    total = sum(sizes)
    usable_tiers = topo.confidence != "low" and len(clusters) > 1

    if usable_tiers:
        prefix: list[int] = []
        for index, cluster in enumerate(clusters[:-1]):  # skip the full set
            prefix.extend(cluster.cpuIds)
            name = "+".join(c.tier for c in clusters[: index + 1])
            candidates.append(
                Candidate(
                    id=f"pin-{name}-t{len(prefix)}",
                    threads=len(prefix),
                    cpuMask=hex_mask(prefix),
                    family="pinned",
                    rationale=(
                        f"pin to the {name} cluster(s) "
                        f"({', '.join(sorted({c.label for c in clusters[: index + 1]}))}); "
                        "no slower core can join the barrier"
                    ),
                    threadsBatch=len(prefix),
                )
            )
        little = clusters[-1]
        candidates.append(
            Candidate(
                id=f"pin-little-t{little.size}",
                threads=little.size,
                cpuMask=hex_mask(little.cpuIds),
                family="pinned",
                rationale=(
                    f"pin to the {little.label} cluster alone; on a DRAM-bound decode "
                    "the little cores can saturate bandwidth without the big cores"
                ),
                threadsBatch=little.size,
            )
        )

    for threads in _boundary_thread_counts(sizes):
        candidates.append(
            Candidate(
                id=f"t{threads}",
                threads=threads,
                cpuMask=None,
                family="unpinned",
                rationale=(
                    f"{threads}/{total} threads, scheduler-placed; derived from a "
                    "cluster boundary or the midpoint between two boundaries"
                ),
                threadsBatch=threads,
            )
        )

    # Prefill is compute-heavy, so include topology-wide prefill paired with
    # every narrower decode boundary. This adds the phase-aware candidates the
    # old single-policy sweep could not represent, while avoiding an expensive
    # and poorly justified Cartesian product of every possible width.
    for decode_threads in _boundary_thread_counts(sizes):
        if decode_threads >= total:
            continue
        candidates.append(
            Candidate(
                id=f"pp{total}-tg{decode_threads}",
                threads=decode_threads,
                cpuMask=None,
                family="phase-pair",
                rationale=(
                    f"use all {total} discovered cores for compute-heavy prefill "
                    f"and {decode_threads} topology-derived threads for decode"
                ),
                threadsBatch=total,
            )
        )

    seen: set[tuple[int | None, int | None, str | None]] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.threads, candidate.threadsBatch, candidate.cpuMask)
        if key in seen and candidate.family != "stock":
            continue
        seen.add(key)
        unique.append(candidate)

    if max_candidates is not None and len(unique) > max_candidates:
        if max_candidates < 2:
            raise AutotuneError("--max-candidates must be >= 2 (stock + one rival)")
        # Always keep stock; thin the rest evenly so the survivors still span the
        # whole thread range rather than clustering at one end.
        rest = unique[1:]
        keep = max_candidates - 1
        step = len(rest) / keep
        unique = [unique[0]] + [rest[int(i * step)] for i in range(keep)]
    return unique


# ---------------------------------------------------------------------------
# llama-bench invocation
# ---------------------------------------------------------------------------

def build_bench_cmd(
    candidate: Candidate, defaults: dict[str, Any], model_file: str, repetitions: int
) -> tuple[list[str], str]:
    """Return (adb_args, human shell string) for one candidate.

    Deliberately not `run_suite.build_bench_cmd`: that one requires an explicit
    `threads` (the stock candidate must omit `-t` entirely) and emits one test per
    case, whereas the sweep wants pp and tg from a single invocation to halve the
    wall-clock and the thermal exposure.
    """
    bin_dir = defaults["binDir"]
    binary = defaults.get("binary", "llama-bench")
    parts = [
        f"./{binary}",
        "-m", f"{defaults['modelsDir']}/{model_file}",
    ]
    if candidate.threads is not None:
        parts += ["-t", str(candidate.threads)]
    if candidate.threadsBatch is not None and candidate.threadsBatch != candidate.threads:
        parts += ["-tb", str(candidate.threadsBatch)]
    parts += [
        "-ngl", str(defaults.get("nGpuLayers", 0)),
        "-c", str(defaults["contextSize"]),
        "-r", str(repetitions),
        "-p", str(defaults["promptTokens"]),
        "-n", str(defaults["genTokens"]),
    ]
    if candidate.cpuMask:
        parts += ["-C", candidate.cpuMask]
    if defaults.get("warmup") is False:
        parts.append("--no-warmup")
    parts += ["-o", "json"]
    shell = f"cd {bin_dir} && LD_LIBRARY_PATH={bin_dir} " + " ".join(parts)
    return ["shell", shell], shell


def parse_bench_output(output: str) -> tuple[list[dict], str | None]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        return [], str(exc)
    if not isinstance(parsed, list):
        return [], "llama-bench JSON was not an array"
    return parsed, None


# ---------------------------------------------------------------------------
# Thermal gating
# ---------------------------------------------------------------------------

THERMAL_STATUS_ADVISORY = (
    "android.os.PowerManager thermal status is recorded but does NOT gate the "
    "sweep by default. On the test device (Redmi Note 14 5G / MT6855) status 2 "
    "(MODERATE) is the *steady state*, not a throttling signal: it is reported "
    "at every telemetry point of runs that were thermally quiet by temperature "
    "(the 27 Jul ngram A/B sat at status 2 throughout with a 0.2 C battery "
    "temperature rise), partly because the device is USB-charging during every "
    "benchmark to hold stay_on_while_plugged_in. Gating on status would return "
    "'inconclusive' on every sweep forever. Temperature, measured relative to "
    "the sweep's own starting point, is the discriminating signal. Opt into a "
    "strict status gate with --max-thermal-status N on devices where the status "
    "is meaningful."
)


def thermal_code(snapshot: dict[str, Any] | None) -> int | None:
    """Pull the numeric PowerManager thermal status out of a run_suite snapshot
    (`"2 (MODERATE)"` -> 2). Returns None when unknown."""
    if not snapshot:
        return None
    value = snapshot.get("thermalStatus")
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else None


def thermal_ok(
    snapshot: dict[str, Any],
    ceiling_c: float | None,
    max_status: int | None = None,
) -> tuple[bool, list[str]]:
    """Is the device cool enough to start the next candidate?

    Temperature against `ceiling_c` is the real check. Thermal status only
    participates when the caller explicitly sets `max_status` — see
    THERMAL_STATUS_ADVISORY for why gating on it by default is wrong here.
    """
    reasons: list[str] = []
    if max_status is not None:
        code = thermal_code(snapshot)
        if code is not None and code > max_status:
            reasons.append(
                f"thermalStatus={snapshot.get('thermalStatus')} > {max_status}"
            )
    temp = snapshot.get("batteryTempC")
    if ceiling_c is not None and isinstance(temp, (int, float)) and temp > ceiling_c:
        reasons.append(f"batteryTempC={temp:.1f} > {ceiling_c:.1f}")
    return (not reasons), reasons


def wait_for_thermal(
    serial: str | None,
    dry_run: bool,
    ceiling_c: float | None,
    cooldown_s: int,
    max_wait_s: int,
    poll_s: int = 15,
    sleep=time.sleep,
    max_status: int | None = None,
) -> dict[str, Any]:
    """Idle `cooldown_s`, then poll until the device is back under `ceiling_c`
    (and, if `max_status` is set, under that thermal status), or `max_wait_s`
    elapses.

    A fixed sleep is not enough on its own: temperature can still be climbing
    when the timer expires, and the next candidate then measures the tail of the
    previous one.
    """
    record: dict[str, Any] = {
        "cooldownSeconds": cooldown_s,
        "ceilingC": ceiling_c,
        "maxThermalStatus": max_status,
        "maxWaitSeconds": max_wait_s,
        "waitedSeconds": 0,
        "polls": [],
        "gateSatisfied": None,
        "timedOut": False,
    }
    if dry_run:
        run_suite.capture_thermal(serial, True)
        record["dryRun"] = True
        return record
    if cooldown_s > 0:
        sleep(cooldown_s)
        record["waitedSeconds"] += cooldown_s
    waited = 0
    while True:
        snapshot = run_suite.capture_thermal(serial, False)
        ok, reasons = thermal_ok(snapshot, ceiling_c, max_status)
        record["polls"].append(
            {
                "batteryTempC": snapshot.get("batteryTempC"),
                "thermalStatus": snapshot.get("thermalStatus"),
                "ok": ok,
                "reasons": reasons,
                "at": snapshot.get("capturedAt"),
            }
        )
        if ok:
            record["gateSatisfied"] = True
            return record
        if waited >= max_wait_s:
            record["gateSatisfied"] = False
            record["timedOut"] = True
            return record
        sleep(poll_s)
        waited += poll_s
        record["waitedSeconds"] += poll_s


# ---------------------------------------------------------------------------
# Sweep execution
# ---------------------------------------------------------------------------

def round_order(candidates: list[Candidate], round_index: int) -> list[Candidate]:
    """Counterbalance the execution order: forward, reversed, forward, ...

    With N rounds this gives every candidate roughly the same mean position in
    the sweep, so residual drift is shared instead of being charged entirely to
    whichever candidate happens to run last.
    """
    return list(candidates) if round_index % 2 == 0 else list(reversed(candidates))


def run_candidate(
    candidate: Candidate,
    defaults: dict[str, Any],
    model_file: str,
    repetitions: int,
    serial: str | None,
    dry_run: bool,
    round_index: int,
    position: int,
) -> dict[str, Any]:
    adb_args, shell = build_bench_cmd(candidate, defaults, model_file, repetitions)
    thermal_start = run_suite.capture_thermal(serial, dry_run)
    label = "warm-up" if round_index < 0 else f"round {round_index + 1}"
    print(f"  [{label}] {candidate.id:<24} {candidate.flags()}")
    output = run_adb(serial, adb_args, dry_run, timeout=1800)
    thermal_end = run_suite.capture_thermal(serial, dry_run)

    bench, parse_error = ([], None) if dry_run else parse_bench_output(output)
    resolved_contexts = {
        entry.get("n_ctx") for entry in bench if isinstance(entry.get("n_ctx"), int)
    }
    record: dict[str, Any] = {
        "candidateId": candidate.id,
        "round": round_index + 1,
        "position": position,
        "command": shell,
        "startedAt": thermal_start.get("capturedAt"),
        "thermalStart": thermal_start,
        "thermalEnd": thermal_end,
        "metrics": run_suite.metrics_from_bench(bench),
        "resolvedThreads": (bench[0].get("n_threads") if bench else None),
        "resolvedThreadsBatch": (
            bench[0].get("n_threads_batch", bench[0].get("n_threads")) if bench else None
        ),
        "resolvedContextSize": (
            next(iter(resolved_contexts)) if len(resolved_contexts) == 1 else None
        ),
        "llamaCppCommit": (bench[0].get("build_commit") if bench else None),
        "ok": bool(bench) if not dry_run else None,
        "rawBench": bench,
    }
    if parse_error:
        record["benchParseError"] = parse_error
        record["rawBenchText"] = output[:4000]
        record["ok"] = False
    elif bench and resolved_contexts != {defaults["contextSize"]}:
        record["contextError"] = (
            f"llama-bench resolved contexts {sorted(resolved_contexts)}; "
            f"expected {defaults['contextSize']}"
        )
        record["ok"] = False
    return record


def sweep(
    candidates: list[Candidate],
    defaults: dict[str, Any],
    model_file: str,
    serial: str | None,
    dry_run: bool,
    *,
    rounds: int,
    repetitions: int,
    cooldown_s: int,
    thermal_ceiling_c: float | None,
    thermal_max_wait_s: int,
    max_thermal_status: int | None = None,
    sleep=time.sleep,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run `rounds` counterbalanced passes; return (records, cooldown records)."""
    records: list[dict[str, Any]] = []
    cooldowns: list[dict[str, Any]] = []
    first = True
    for round_index in range(rounds):
        for position, candidate in enumerate(round_order(candidates, round_index)):
            if not first:
                gate = wait_for_thermal(
                    serial, dry_run, thermal_ceiling_c, cooldown_s,
                    thermal_max_wait_s, sleep=sleep,
                    max_status=max_thermal_status,
                )
                gate["beforeCandidateId"] = candidate.id
                gate["beforeRound"] = round_index + 1
                cooldowns.append(gate)
                if gate.get("gateSatisfied") is False:
                    print(
                        f"  ! thermal gate timed out before {candidate.id}; "
                        "the sweep will be flagged"
                    )
            first = False
            records.append(
                run_candidate(
                    candidate, defaults, model_file, repetitions,
                    serial, dry_run, round_index, position,
                )
            )
    return records, cooldowns


def warmup_run(
    candidate: Candidate,
    defaults: dict[str, Any],
    model_file: str,
    serial: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """One discarded invocation to page in the GGUF.

    Cold-cache first runs measured 1.11 tok/s against 10.50 tok/s warm on the
    test device — an order of magnitude, and it would land entirely on whichever
    candidate ran first. Recorded for the audit trail, never scored.
    """
    record = run_candidate(candidate, defaults, model_file, 1, serial, dry_run, -1, 0)
    record["scored"] = False
    record["purpose"] = "warmup (discarded; primes the page cache)"
    record["candidateId"] = candidate.id
    return record


# ---------------------------------------------------------------------------
# Aggregation, scoring, decision
# ---------------------------------------------------------------------------

METRIC_KEYS = ("ppTokensPerSec", "tgTokensPerSec")


def _pooled(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"mean": None, "stddev": None, "relStddev": None, "samples": []}
    mean = statistics.fmean(samples)
    stddev = statistics.stdev(samples) if len(samples) >= 2 else 0.0
    return {
        "mean": mean,
        "stddev": stddev,
        "relStddev": (stddev / mean) if mean else None,
        "samples": samples,
    }


def aggregate_rounds(
    candidates: list[Candidate], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pool every repetition of every round into one row per candidate.

    Samples are pooled rather than averaging per-round means, so the reported
    stddev includes between-round variation — which is exactly the drift and
    tail behaviour the scoring rule is meant to punish.
    """
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("scored") is False:
            continue
        by_id.setdefault(record["candidateId"], []).append(record)

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        entries = by_id.get(candidate.id, [])
        metrics: dict[str, Any] = {}
        for key in METRIC_KEYS:
            samples: list[float] = []
            for entry in entries:
                stat = (entry.get("metrics") or {}).get(key) or {}
                values = stat.get("samples")
                if isinstance(values, list) and values:
                    samples.extend(float(v) for v in values if isinstance(v, (int, float)))
                elif isinstance(stat.get("mean"), (int, float)):
                    samples.append(float(stat["mean"]))
            metrics[key] = _pooled(samples)
        resolved = [e.get("resolvedThreads") for e in entries if e.get("resolvedThreads")]
        resolved_batch = [
            e.get("resolvedThreadsBatch") for e in entries if e.get("resolvedThreadsBatch")
        ]
        rows.append(
            {
                **candidate.to_json(),
                "flags": candidate.flags(),
                "rounds": len(entries),
                "ok": bool(entries) and all(e.get("ok") is not False for e in entries),
                "resolvedThreads": resolved[0] if resolved else None,
                "resolvedThreadsBatch": resolved_batch[0] if resolved_batch else None,
                "metrics": metrics,
                "perRound": [
                    {
                        "round": e.get("round"),
                        "position": e.get("position"),
                        "pp": ((e.get("metrics") or {}).get("ppTokensPerSec") or {}).get("mean"),
                        "tg": ((e.get("metrics") or {}).get("tgTokensPerSec") or {}).get("mean"),
                        "thermalStartC": (e.get("thermalStart") or {}).get("batteryTempC"),
                        "thermalEndC": (e.get("thermalEnd") or {}).get("batteryTempC"),
                        "thermalStatusEnd": (e.get("thermalEnd") or {}).get("thermalStatus"),
                    }
                    for e in entries
                ],
            }
        )
    return rows


SCORING_RULE_DOC = (
    "score(metric) = mean - k*stddev, pooled over all rounds; expressed as a "
    "ratio to the stock-default row; combined = tgWeight*tgRatio + "
    "(1-tgWeight)*ppRatio. Any row whose relative stddev exceeds maxRelStddev on "
    "a measured metric is disqualified from winning. The winner must beat stock "
    "by at least minImprovement."
)


def score_rows(
    rows: list[dict[str, Any]],
    *,
    stability_k: float = 1.0,
    tg_weight: float = 0.7,
    max_rel_stddev: float = 0.10,
    baseline_id: str = STOCK_CANDIDATE.id,
) -> dict[str, Any]:
    """Rank candidates on a variance-penalised, stock-relative score.

    The rule, in full:

      robust(metric) = mean - k * stddev            (k = --stability-k, default 1)
      ratio(metric)  = robust(candidate) / robust(stock)
      combined       = w * ratio(tg) + (1 - w) * ratio(pp)     (w = --tg-weight)
      disqualified   = relStddev(metric) > --max-rel-stddev on any measured metric

    Why `mean - k*stddev` and not the mean. Phase 1 measured t=8 at
    58.62 +- 26.74 tok/s (reps 74.8, 78.9, 78.1, 19.6, 41.7): the *highest* peak
    and an indefensible tail. A user feels the tail, not the mean, so the score
    compares configurations at roughly their one-sigma-down outcome. That is a
    p99-latency argument, and it is measurable on any device (docs/PLAN.md,
    Phase 3B: "report variance, not just the mean").

    Why a hard disqualification on top of the penalty. `mean - k*sigma` is still
    a smooth trade: a large enough mean can buy back an unstable tail, and a
    bimodal collapse like t=8's is badly summarised by sigma anyway. The
    relative-stddev gate makes "no wild configs win, at any speed" a rule rather
    than a coefficient someone can tune away.

    Why ratios to stock rather than raw tok/s. pp and tg differ by ~7x in
    magnitude on this device, so summing raw values would let prefill silently
    decide a decode question. Normalising against stock also makes the number
    the report actually needs to state ("+12% vs stock defaults") the same number
    the tool optimises.

    Why tg is weighted 0.7. Decode dominates perceived speed for chat, and
    Phase 2 showed decode is the DRAM-bound phase where core policy matters most.
    Tunable with --tg-weight for prefill-heavy workloads.

    Rows are annotated in place; the returned dict describes the ranking.
    """
    baseline = next((r for r in rows if r["id"] == baseline_id), None)

    def robust(row: dict[str, Any], key: str) -> float | None:
        stat = (row.get("metrics") or {}).get(key) or {}
        mean, stddev = stat.get("mean"), stat.get("stddev")
        if not isinstance(mean, (int, float)):
            return None
        return float(mean) - stability_k * float(stddev or 0.0)

    baseline_robust = {key: (robust(baseline, key) if baseline else None) for key in METRIC_KEYS}
    normaliser_source = "stock-default"
    if not any(isinstance(v, (int, float)) and v > 0 for v in baseline_robust.values()):
        # No usable baseline: fall back to the best row per metric so the ranking
        # is still meaningful, and say so in the output.
        normaliser_source = "best-measured-row"
        for key in METRIC_KEYS:
            values = [v for v in (robust(r, key) for r in rows) if isinstance(v, (int, float)) and v > 0]
            baseline_robust[key] = max(values) if values else None

    for row in rows:
        reasons: list[str] = []
        ratios: dict[str, float | None] = {}
        for key in METRIC_KEYS:
            value = robust(row, key)
            base = baseline_robust.get(key)
            ratios[key] = (
                value / base
                if isinstance(value, (int, float)) and isinstance(base, (int, float)) and base > 0
                else None
            )
            stat = (row.get("metrics") or {}).get(key) or {}
            rel = stat.get("relStddev")
            if isinstance(rel, (int, float)) and rel > max_rel_stddev:
                reasons.append(
                    f"{key} relative stddev {rel:.1%} > {max_rel_stddev:.1%}"
                )
        weights = {"tgTokensPerSec": tg_weight, "ppTokensPerSec": 1.0 - tg_weight}
        usable = {k: v for k, v in ratios.items() if isinstance(v, (int, float))}
        if usable:
            weight_sum = sum(weights[k] for k in usable) or 1.0
            combined = sum(weights[k] * v for k, v in usable.items()) / weight_sum
        else:
            combined = None
            reasons.append("no usable metrics")
        if row.get("ok") is False:
            reasons.append("bench invocation failed")
        row["score"] = {
            "robust": {key: robust(row, key) for key in METRIC_KEYS},
            "ratioVsBaseline": ratios,
            "combined": combined,
            "partialMetrics": len(usable) < len(METRIC_KEYS),
            "disqualified": bool(reasons),
            "disqualificationReasons": reasons,
        }

    eligible = [r for r in rows if not r["score"]["disqualified"] and r["score"]["combined"] is not None]
    ranked = sorted(
        eligible,
        key=lambda r: (
            -r["score"]["combined"],
            # Tie-breaks: prefer the steadier config, then the cheaper one.
            sum(
                ((r["metrics"].get(k) or {}).get("relStddev") or 0.0) for k in METRIC_KEYS
            ),
            (r.get("threads") or math.inf) + (r.get("threadsBatch") or math.inf),
        ),
    )
    return {
        "rule": SCORING_RULE_DOC,
        "stabilityK": stability_k,
        "tgWeight": tg_weight,
        "maxRelStddev": max_rel_stddev,
        "baselineId": baseline_id,
        "normaliser": normaliser_source,
        "ranking": [r["id"] for r in ranked],
        "bestId": ranked[0]["id"] if ranked else None,
        "disqualified": [
            {"id": r["id"], "reasons": r["score"]["disqualificationReasons"]}
            for r in rows
            if r["score"]["disqualified"]
        ],
    }


def thermal_assessment(
    records: list[dict[str, Any]],
    cooldowns: list[dict[str, Any]],
    *,
    max_temp_rise_c: float = 3.0,
    max_thermal_status: int | None = None,
) -> dict[str, Any]:
    """Decide whether the sweep was thermally clean enough to trust.

    **Temperature is the discriminating signal**: a rise across the sweep larger
    than `max_temp_rise_c`, measured relative to the sweep's own starting point,
    means the ranking may be an artefact of run order rather than configuration.
    Thermal-gate timeouts count too, since they are themselves temperature-driven.

    **Thermal status is recorded, reported and warned about, but does not gate**
    unless the caller passes `max_thermal_status`. See THERMAL_STATUS_ADVISORY:
    on the target device MODERATE is the resting state while USB-charging, so a
    status gate would mark every sweep inconclusive and no profile would ever be
    cached. `maxThermalStatus` and `thermalStatuses` stay in the report so the
    information is never lost, just not treated as proof of throttling.
    """
    temps: list[float] = []
    statuses: list[str] = []
    codes: list[int] = []
    for record in records:
        for key in ("thermalStart", "thermalEnd"):
            snapshot = record.get(key) or {}
            temp = snapshot.get("batteryTempC")
            if isinstance(temp, (int, float)):
                temps.append(float(temp))
            status = snapshot.get("thermalStatus")
            if isinstance(status, str):
                statuses.append(status)
            code = thermal_code(snapshot)
            if code is not None:
                codes.append(code)
    for gate in cooldowns:
        for poll in gate.get("polls", []):
            temp = poll.get("batteryTempC")
            if isinstance(temp, (int, float)):
                temps.append(float(temp))
            code = thermal_code(poll)
            if code is not None:
                codes.append(code)
            status = poll.get("thermalStatus")
            if isinstance(status, str):
                statuses.append(status)

    start = temps[0] if temps else None
    peak = max(temps) if temps else None
    end = temps[-1] if temps else None
    rise = (peak - start) if isinstance(peak, float) and isinstance(start, float) else None
    worst_status = max(codes) if codes else None
    gate_timeouts = [g["beforeCandidateId"] for g in cooldowns if g.get("timedOut")]

    reasons: list[str] = []
    warnings: list[str] = []
    # Advisory only unless the caller opted into a status gate.
    status_exceeded = (
        None
        if max_thermal_status is None or worst_status is None
        else worst_status > max_thermal_status
    )
    if worst_status is not None and worst_status > 1:
        warnings.append(
            f"peak thermal status was {worst_status}; recorded, not gated "
            "(pass --max-thermal-status to make it a gate)"
        )
    if status_exceeded:
        reasons.append(
            f"thermal status reached {worst_status} > --max-thermal-status "
            f"{max_thermal_status}"
        )
    if isinstance(rise, float) and rise > max_temp_rise_c:
        reasons.append(f"battery temperature rose {rise:.1f} C > {max_temp_rise_c:.1f} C")
    if gate_timeouts:
        reasons.append(
            "thermal gate timed out before: " + ", ".join(sorted(set(gate_timeouts)))
        )
    stable = None if not temps and not codes else not reasons
    return {
        "startBatteryTempC": start,
        "peakBatteryTempC": peak,
        "endBatteryTempC": end,
        "batteryTempRiseC": rise,
        "maxTempRiseLimitC": max_temp_rise_c,
        "thermalStatuses": list(dict.fromkeys(statuses)),
        "maxThermalStatus": worst_status,
        "maxThermalStatusLimit": max_thermal_status,
        "thermalStatusGated": max_thermal_status is not None,
        "statusExceeded": status_exceeded,
        "statusAdvisory": THERMAL_STATUS_ADVISORY,
        "gateTimeouts": gate_timeouts,
        "stable": stable,
        "warnings": warnings,
        "reasons": reasons,
    }


def order_drift(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Correlate measured speed with position in the round.

    An independent, thermometer-free check on the same hazard: if every
    candidate is slower the later it ran, the sweep measured run order, not
    configuration. Reported as the mean per-round first-vs-last ratio.
    """
    ratios: list[float] = []
    by_round: dict[int, list[tuple[int, float]]] = {}
    for row in rows:
        for entry in row.get("perRound", []):
            value = entry.get("tg") if entry.get("tg") is not None else entry.get("pp")
            if isinstance(value, (int, float)) and entry.get("round") is not None:
                by_round.setdefault(int(entry["round"]), []).append(
                    (int(entry.get("position") or 0), float(value))
                )
    for entries in by_round.values():
        if len(entries) < 2:
            continue
        entries.sort()
        first, last = entries[0][1], entries[-1][1]
        if first > 0:
            ratios.append(last / first)
    mean_ratio = statistics.fmean(ratios) if ratios else None
    return {
        "roundsCompared": len(ratios),
        "lastOverFirstRatios": ratios,
        "meanLastOverFirst": mean_ratio,
    }


def decide(
    rows: list[dict[str, Any]],
    ranking: dict[str, Any],
    thermal: dict[str, Any],
    drift: dict[str, Any],
    *,
    min_improvement: float = 0.03,
    max_order_drift: float = 0.10,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Combine ranking + measurement hygiene into gates, winner and verdict.

    A thermally drifted sweep never crowns a winner. That is the whole point of
    the gate: a 2.5x apparent difference produced by run order would otherwise be
    reported as a spectacular optimisation.
    """
    best = next((r for r in rows if r["id"] == ranking.get("bestId")), None)
    stock = next((r for r in rows if r["id"] == STOCK_CANDIDATE.id), None)
    improvement = (
        best["score"]["combined"] - 1.0
        if best is not None and best["score"]["combined"] is not None
        else None
    )

    order_ratio = drift.get("meanLastOverFirst")
    order_ok = (
        None
        if not isinstance(order_ratio, (int, float))
        else abs(1.0 - order_ratio) <= max_order_drift
    )

    gates = {
        "allCandidatesMeasured": bool(rows) and all(r.get("ok") is not False for r in rows),
        "baselineMeasured": bool(stock)
        and any(
            isinstance((stock["metrics"].get(k) or {}).get("mean"), (int, float))
            for k in METRIC_KEYS
        ),
        "thermallyStable": thermal.get("stable"),
        "runOrderNeutral": order_ok,
        "winnerIsStable": (best is not None) and not best["score"]["disqualified"],
        "beatsStockDefault": (
            improvement >= min_improvement if isinstance(improvement, float) else None
        ),
    }

    trustworthy = gates["thermallyStable"] is not False and gates["runOrderNeutral"] is not False
    if dry_run:
        # Nothing was measured, so no gate has an opinion. Reporting them as
        # failures would make a preview look like a broken run.
        gates = {name: None for name in gates}
        verdict = "inconclusive"
        reason = "dry run: no measurements were taken"
        winner = None
    elif not trustworthy:
        verdict = "inconclusive"
        reason = (
            "sweep is thermally or order-contaminated; refusing to name a winner: "
            + "; ".join(thermal.get("reasons", []) or ["run order correlated with speed"])
        )
        winner = None
    elif gates["allCandidatesMeasured"] is False or gates["baselineMeasured"] is False:
        verdict = "fail"
        reason = "one or more candidates (or the stock baseline) failed to measure"
        winner = None
    elif best is None:
        verdict = "inconclusive"
        reason = "every candidate was disqualified for instability"
        winner = None
    elif gates["beatsStockDefault"]:
        verdict = "autotuned"
        reason = f"{best['id']} beats stock defaults by {improvement:.1%}"
        winner = best
    else:
        verdict = "stock-default-optimal"
        reason = (
            "no candidate beat stock defaults by the required margin "
            f"({min_improvement:.1%}); keeping stock"
        )
        winner = stock

    recommendation = {
        "source": "sweep" if verdict == "autotuned" else "stock-default",
        "candidateId": (winner or STOCK_CANDIDATE.to_json())["id"] if winner else STOCK_CANDIDATE.id,
        "threads": winner["threads"] if winner else None,
        "threadsBatch": winner.get("threadsBatch") if winner else None,
        "baselineThreads": stock.get("resolvedThreads") if stock else None,
        "baselineThreadsBatch": stock.get("resolvedThreadsBatch") if stock else None,
        "cpuMask": winner["cpuMask"] if winner else None,
        "benchFlags": winner["flags"] if winner else STOCK_CANDIDATE.flags(),
        "improvementVsStock": improvement if verdict == "autotuned" else 0.0,
    }
    return {
        "gates": gates,
        "verdict": verdict,
        "reason": reason,
        "winner": winner,
        "recommendation": recommendation,
        "minImprovement": min_improvement,
        "maxOrderDrift": max_order_drift,
    }


# ---------------------------------------------------------------------------
# Fingerprinting, cache, bundled profiles
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def device_fingerprint(device: dict[str, Any], topo: Topology) -> str:
    """Stable identity for a (handset, CPU layout) pair.

    Deliberately excludes the adb serial so the same phone model with the same
    topology can share a bundled profile, and excludes anything mutable
    (temperature, free memory, Android patch level).
    """
    payload = {
        "model": (device.get("model") or "").strip().lower(),
        "soc": (device.get("soc") or "").strip().lower(),
        "coreCount": topo.coreCount,
        "clusterSignature": topo.cluster_signature(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(
    device_fp: str,
    model_sha256: str | None,
    profile_identity_sha: str | None = None,
) -> str:
    """Readable key; the optional identity suffix makes stale profiles miss."""
    key = f"{device_fp[:16]}:{(model_sha256 or 'unknown-model')[:16]}"
    return f"{key}:{profile_identity_sha[:16]}" if profile_identity_sha else key


def benchmark_binary_identity(
    serial: str | None, defaults: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    """Fingerprint the executable that will run, rather than trusting the checkout."""
    bin_dir = defaults["binDir"]
    binary = defaults["binary"]
    command = f"cd {bin_dir} && sha256sum ./{binary}"
    output = run_adb(serial, ["shell", command], dry_run, timeout=60)
    match = re.search(r"\b([0-9a-fA-F]{64})\b", output or "")
    source_commit = None
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT / "third_party" / "llama.cpp"), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        source_commit = result.stdout.strip() or None
    except Exception:
        pass
    return {
        "path": f"{bin_dir}/{binary}",
        "sha256": match.group(1).lower() if match else None,
        "llamaCppSourceCommit": source_commit,
    }


def build_profile_identity(
    defaults: dict[str, Any], model_sha: str | None, binary: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    identity = {
        "schemaVersion": 1,
        "binarySha256": binary.get("sha256"),
        "llamaCppSourceCommit": binary.get("llamaCppSourceCommit"),
        "modelSha256": model_sha,
        "contextSize": int(defaults["contextSize"]),
        "benchmarkShape": {
            "promptTokens": defaults["promptTokens"],
            "genTokens": defaults["genTokens"],
            "repetitions": defaults["repetitions"],
            "rounds": defaults["rounds"],
        },
        "workloadClass": defaults.get("workloadClass", "interactive-chat"),
        "scoring": defaults["scoring"],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return identity, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def profile_match_fields(
    device: dict[str, Any],
    topo: Topology,
    model_sha: str | None,
    profile_identity_sha: str | None = None,
) -> dict[str, Any]:
    return {
        "socModel": device.get("soc"),
        "deviceModel": device.get("model"),
        "coreCount": topo.coreCount,
        "midrSignature": topo.midr_signature(),
        "clusterSignature": topo.cluster_signature(),
        "modelSha256": model_sha,
        "profileIdentitySha256": profile_identity_sha,
    }


def match_known_profile(
    table: dict[str, Any],
    fields: dict[str, Any],
    key: str,
    *,
    trust_low_confidence: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Find a bundled profile for this device+model.

    Every key present in an entry's `match` block must equal the derived value
    (strings compared case-insensitively); absent keys are wildcards, so an entry
    can be seeded without inventing values the author never measured. Entries at
    `confidence: "low"` are returned as advisory notes only, and the sweep still
    runs unless --trust-known-profiles is given.
    """
    notes: list[str] = []
    for entry in table.get("profiles", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("key") and entry["key"] != key:
            continue
        criteria = entry.get("match", {})
        if not isinstance(criteria, dict):
            continue
        matched = True
        for name, expected in criteria.items():
            actual = fields.get(name)
            if isinstance(expected, str) and isinstance(actual, str):
                if expected.strip().lower() != actual.strip().lower():
                    matched = False
                    break
            elif expected != actual:
                matched = False
                break
        if not matched:
            continue
        confidence = str(entry.get("confidence", "low")).lower()
        identity_bound = bool(criteria.get("profileIdentitySha256"))
        if trust_low_confidence or (confidence == "high" and identity_bound):
            return entry, notes
        notes.append(
            f"known profile '{entry.get('id', '?')}' matched but is confidence={confidence} "
            f"and full profile identity is {'bound' if identity_bound else 'not bound'}; sweeping anyway "
            "(pass --trust-known-profiles to accept it)"
        )
    return None, notes


def cache_lookup(cache: dict[str, Any], key: str) -> dict[str, Any] | None:
    entry = (cache.get("entries") or {}).get(key)
    return entry if isinstance(entry, dict) else None


def cache_store(cache_path: Path, key: str, record: dict[str, Any]) -> None:
    cache = load_json_file(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("schemaVersion", 1)
    cache.setdefault(
        "note",
        "Autotuner profile cache keyed by <deviceFingerprint16>:<modelSha16>:"
        "<profileIdentity16>. Binary, workload, context/shape, or scoring changes "
        "therefore miss automatically; --force still re-sweeps an exact match.",
    )
    cache.setdefault("entries", {})
    cache["entries"][key] = record
    cache["updatedAt"] = now_utc()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_table(rows: list[dict[str, Any]]) -> None:
    header = f"{'candidate':<24}{'flags':<20}{'pp tok/s':>18}{'tg tok/s':>18}{'score':>9}  note"
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        def cell(key: str) -> str:
            stat = (row.get("metrics") or {}).get(key) or {}
            mean, stddev = stat.get("mean"), stat.get("stddev")
            if not isinstance(mean, (int, float)):
                return "-"
            return f"{mean:.2f} +- {stddev or 0.0:.2f}"

        score = row.get("score", {})
        combined = score.get("combined")
        note = "DQ: " + score["disqualificationReasons"][0] if score.get("disqualified") else ""
        print(
            f"{row['id']:<24}{row.get('flags', ''):<20}"
            f"{cell('ppTokensPerSec'):>18}{cell('tgTokensPerSec'):>18}"
            f"{(f'{combined:.3f}' if isinstance(combined, (int, float)) else '-'):>9}  {note}"
        )


def estimate_minutes(candidates: int, rounds: int, cooldown_s: int, bench_s: int = 25) -> float:
    invocations = candidates * rounds
    return (invocations * bench_s + max(invocations - 1, 0) * cooldown_s) / 60.0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Measure the best thread/affinity policy for this device+model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=SCORING_RULE_DOC,
    )
    ap.add_argument("--suite", default=str(DEFAULT_SUITE), help="autotune suite JSON")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the adb commands without running them (needs no device)")
    ap.add_argument("--model", default=None, help="model id from models/manifest.json")
    ap.add_argument("--topology-file", default=None,
                    help="JSON with cpuinfo/cpufreq/present/meminfo text, instead of reading a device")
    ap.add_argument("--rounds", type=int, default=None, help="counterbalanced passes over the candidates")
    ap.add_argument("--reps", type=int, default=None, help="llama-bench -r per candidate per round")
    ap.add_argument("--pp", type=int, default=None, help="prompt tokens (small: seconds, not minutes)")
    ap.add_argument("--tg", type=int, default=None, help="generated tokens")
    ap.add_argument("--context-size", type=int, default=None,
                    help="explicit llama-bench context allocation (must cover pp + tg)")
    ap.add_argument("--cooldown", type=int, default=None,
                    help="idle seconds before each candidate (default 120, as in run_suite.py)")
    ap.add_argument("--thermal-max-wait", type=int, default=None,
                    help="max extra seconds to wait for the thermal gate")
    ap.add_argument("--temp-slack-c", type=float, default=None,
                    help="allowed battery temperature above the sweep start before proceeding")
    ap.add_argument("--max-temp-rise-c", type=float, default=None,
                    help="flag the sweep if temperature rises more than this")
    ap.add_argument("--max-thermal-status", type=int, default=None,
                    help="opt in to gating on android.os.PowerManager thermal status "
                         "(e.g. 1 = allow up to LIGHT). Default: record only, do not "
                         "gate -- MODERATE is the resting state on some charging devices")
    ap.add_argument("--no-thermal-gate", action="store_true",
                    help="idle only, do not poll for cooldown (not recommended)")
    ap.add_argument("--stability-k", type=float, default=None, help="variance penalty (mean - k*stddev)")
    ap.add_argument("--tg-weight", type=float, default=None, help="decode weight in the combined score")
    ap.add_argument("--max-rel-stddev", type=float, default=None,
                    help="disqualify candidates noisier than this")
    ap.add_argument("--min-improvement", type=float, default=None,
                    help="required margin over stock defaults before recommending a change")
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="ignore the cache and re-sweep")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    ap.add_argument("--no-known-profiles", action="store_true")
    ap.add_argument("--trust-known-profiles", action="store_true",
                    help="accept a low-confidence bundled profile instead of sweeping")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument(
        "--export-android-policy",
        nargs="?",
        const=str(export_android_policy.DEFAULT_OUTPUT),
        default=None,
        metavar="KOTLIN_FILE",
        help="after a fresh passing sweep, generate the APK policy source (default path if omitted); "
             "cache/known/failed results disable the generated policy",
    )
    ap.add_argument("--print-topology", action="store_true", help="detect topology, print it, exit")
    return ap


def resolve_defaults(suite: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    defaults = dict(suite.get("defaults", {}))
    scoring = dict(defaults.get("scoring", {}))
    overrides = {
        "rounds": args.rounds, "repetitions": args.reps, "promptTokens": args.pp,
        "genTokens": args.tg, "contextSize": args.context_size,
        "cooldownSeconds": args.cooldown,
        "thermalMaxWaitSeconds": args.thermal_max_wait, "tempSlackC": args.temp_slack_c,
        "maxTempRiseC": args.max_temp_rise_c, "model": args.model,
        "maxThermalStatus": args.max_thermal_status,
    }
    for name, value in overrides.items():
        if value is not None:
            defaults[name] = value
    score_overrides = {
        "stabilityK": args.stability_k, "tgWeight": args.tg_weight,
        "maxRelStddev": args.max_rel_stddev, "minImprovement": args.min_improvement,
    }
    for name, value in score_overrides.items():
        if value is not None:
            scoring[name] = value
    defaults["scoring"] = {
        "stabilityK": scoring.get("stabilityK", 1.0),
        "tgWeight": scoring.get("tgWeight", 0.7),
        "maxRelStddev": scoring.get("maxRelStddev", 0.10),
        "minImprovement": scoring.get("minImprovement", 0.03),
        "maxOrderDrift": scoring.get("maxOrderDrift", 0.10),
    }
    defaults.setdefault("rounds", 3)
    defaults.setdefault("repetitions", 2)
    defaults.setdefault("promptTokens", 64)
    defaults.setdefault("genTokens", 32)
    defaults.setdefault("contextSize", 512)
    defaults.setdefault("cooldownSeconds", 120)
    defaults.setdefault("thermalMaxWaitSeconds", 300)
    defaults.setdefault("tempSlackC", 1.0)
    defaults.setdefault("maxTempRiseC", 3.0)
    defaults.setdefault("nGpuLayers", 0)
    for name in ("binDir", "modelsDir", "binary", "model"):
        if not defaults.get(name):
            raise AutotuneError(f"suite defaults.{name} is required")
    if defaults["rounds"] < 1 or defaults["repetitions"] < 1:
        raise AutotuneError("--rounds and --reps must be >= 1")
    if defaults["contextSize"] < defaults["promptTokens"] + defaults["genTokens"]:
        raise AutotuneError("contextSize must be >= promptTokens + genTokens")
    if not 0.0 <= defaults["scoring"]["tgWeight"] <= 1.0:
        raise AutotuneError("--tg-weight must be between 0 and 1")
    return defaults


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        suite_path = Path(args.suite)
        suite = load_json_file(suite_path, None)
        if not isinstance(suite, dict):
            raise AutotuneError(f"cannot read autotune suite {suite_path}")
        defaults = resolve_defaults(suite, args)

        model_map = run_suite.load_model_map()
        model_entry = model_map.get(defaults["model"])
        if model_entry is None:
            raise AutotuneError(
                f"model id '{defaults['model']}' not in models/manifest.json"
            )

        serial = args.serial or run_suite.first_serial(args.dry_run)
        topology_file = Path(args.topology_file) if args.topology_file else None
        print("reading CPU topology (read-only):")
        topo = read_topology(serial, args.dry_run, topology_file)
        device = run_suite.device_block(serial, None, args.dry_run)

        print(f"  {topo.coreCount} cores, {len(topo.clusters)} cluster(s), "
              f"confidence={topo.confidence}, source={topo.source}")
        for cluster in topo.clusters:
            freq = f"{cluster.maxFreqKhz / 1000:.0f} MHz" if cluster.maxFreqKhz else "freq unknown"
            print(f"    {cluster.tier:<7} {cluster.size}x {cluster.label:<34} "
                  f"cpus={list(cluster.cpuIds)} mask={hex_mask(cluster.cpuIds)} {freq}")
        for note in topo.notes:
            print(f"    ! {note}")
        if args.print_topology:
            print(json.dumps(topo.to_json(), indent=2))
            return 0

        model_sha = model_entry.get("sha256")
        binary_identity = benchmark_binary_identity(serial, defaults, args.dry_run)
        profile_identity, profile_identity_sha = build_profile_identity(
            defaults, model_sha, binary_identity
        )
        fingerprint = device_fingerprint(device, topo)
        key = cache_key(fingerprint, model_sha, profile_identity_sha)
        match_fields = profile_match_fields(
            device, topo, model_sha, profile_identity_sha
        )
        print(f"  device fingerprint {fingerprint[:16]}  cache key {key}")

        provenance = {
            "runnerFile": "tools/autotune.py",
            "runnerFileSha256": sha256_file(Path(__file__).resolve()),
            "suiteFile": str(suite_path),
            "suiteFileSha256": sha256_file(suite_path),
            "manifestFile": "models/manifest.json",
            "manifestFileSha256": sha256_file(REPO_ROOT / "models" / "manifest.json"),
            "knownProfilesFile": args.profiles,
            "knownProfilesFileSha256": sha256_file(Path(args.profiles)),
            "appCommit": run_suite.app_commit(),
            "scoringRule": SCORING_RULE_DOC,
        }
        base_report: dict[str, Any] = {
            "schemaVersion": 1,
            "tool": "autotune",
            "suite": suite.get("suite", "autotune"),
            "timestampUtc": now_utc(),
            "dryRun": args.dry_run,
            "provenance": provenance,
            "device": {**device, "fingerprint": fingerprint},
            "topology": topo.to_json(),
            "model": {
                "id": model_entry.get("id"),
                "file": model_entry.get("file"),
                "sha256": model_sha,
                "quantization": model_entry.get("quantization"),
            },
            "cacheKey": key,
            "profileIdentity": {**profile_identity, "sha256": profile_identity_sha},
            "benchmarkBinary": binary_identity,
        }

        notes: list[str] = []
        identity_complete = bool(binary_identity.get("sha256") and model_sha)
        if not identity_complete:
            notes.append(
                "exact binary/model identity is incomplete; cache lookup and storage are disabled"
            )
            print(f"  ! {notes[-1]}")
        # 1. cache, 2. bundled profiles, 3. sweep.
        if not args.force and not args.dry_run and identity_complete:
            cached = cache_lookup(load_json_file(Path(args.cache), {}), key)
            if cached:
                print(f"cache hit for {key}: {cached.get('recommendation', {}).get('benchFlags')}")
                report = {
                    **base_report,
                    "source": "cache",
                    "recommendation": cached.get("recommendation"),
                    "cachedEntry": cached,
                    "verdict": cached.get("verdict", "autotuned"),
                    "note": "cached profile reused; pass --force to re-sweep",
                }
                report_path = write_report(Path(args.out_dir), report)
                export_requested_policy(args, report_path)
                print_recommendation(report["recommendation"])
                return 0

        if not args.no_known_profiles:
            table = load_json_file(Path(args.profiles), {})
            entry, profile_notes = match_known_profile(
                table if isinstance(table, dict) else {},
                match_fields, key,
                trust_low_confidence=args.trust_known_profiles,
            )
            notes.extend(profile_notes)
            for note in profile_notes:
                print(f"  ! {note}")
            if entry is not None and not args.force:
                print(f"bundled profile hit: {entry.get('id')}")
                report = {
                    **base_report,
                    "source": "known-profile",
                    "recommendation": entry.get("recommendation"),
                    "knownProfile": entry,
                    "verdict": "known-profile",
                    "notes": notes,
                }
                report_path = write_report(Path(args.out_dir), report)
                export_requested_policy(args, report_path)
                print_recommendation(report["recommendation"])
                return 0

        candidates = generate_candidates(topo, args.max_candidates)
        rounds = int(defaults["rounds"])
        reps = int(defaults["repetitions"])
        cooldown = int(defaults["cooldownSeconds"])
        print(f"\n{len(candidates)} candidate(s) derived from topology, "
              f"{rounds} counterbalanced round(s) x {reps} rep(s), "
              f"pp{defaults['promptTokens']}/tg{defaults['genTokens']}")
        for candidate in candidates:
            print(f"    {candidate.id:<24} {candidate.flags():<18} {candidate.rationale}")
        print(f"  estimated wall clock ~{estimate_minutes(len(candidates), rounds, cooldown):.0f} min "
              f"(cooldown {cooldown}s between candidates)"
              + (" [DRY RUN]" if args.dry_run else ""))

        # The thermal ceiling is relative to how the device actually starts, not
        # an absolute number that would be wrong in a different ambient.
        start_thermal = run_suite.capture_thermal(serial, args.dry_run)
        start_temp = start_thermal.get("batteryTempC")
        ceiling = (
            None
            if args.no_thermal_gate or not isinstance(start_temp, (int, float))
            else float(start_temp) + float(defaults["tempSlackC"])
        )
        status_gate = defaults.get("maxThermalStatus")
        print(f"\nsweep start: {start_temp} C, thermal gate ceiling "
              f"{'disabled' if ceiling is None else f'{ceiling:.1f} C'}, "
              f"status gate {'off (recorded only)' if status_gate is None else f'<= {status_gate}'}")

        model_file = model_entry["file"]
        print("warm-up (discarded):")
        warmup = warmup_run(STOCK_CANDIDATE, defaults, model_file, serial, args.dry_run)

        max_status = defaults.get("maxThermalStatus")
        max_status = int(max_status) if max_status is not None else None
        records, cooldowns = sweep(
            candidates, defaults, model_file, serial, args.dry_run,
            rounds=rounds, repetitions=reps, cooldown_s=cooldown,
            thermal_ceiling_c=ceiling,
            thermal_max_wait_s=int(defaults["thermalMaxWaitSeconds"]),
            max_thermal_status=max_status,
        )

        rows = aggregate_rounds(candidates, records)
        scoring = defaults["scoring"]
        ranking = score_rows(
            rows,
            stability_k=float(scoring["stabilityK"]),
            tg_weight=float(scoring["tgWeight"]),
            max_rel_stddev=float(scoring["maxRelStddev"]),
        )
        thermal = thermal_assessment(
            records, cooldowns,
            max_temp_rise_c=float(defaults["maxTempRiseC"]),
            max_thermal_status=max_status,
        )
        for warning in thermal.get("warnings", []):
            print(f"  ! {warning}")
        drift = order_drift(rows)
        outcome = decide(
            rows, ranking, thermal, drift,
            min_improvement=float(scoring["minImprovement"]),
            max_order_drift=float(scoring["maxOrderDrift"]),
            dry_run=args.dry_run,
        )

        stock_row = next((r for r in rows if r["id"] == STOCK_CANDIDATE.id), None)
        report = {
            **base_report,
            "source": "sweep",
            "sweep": {
                "rounds": rounds,
                "repetitionsPerRound": reps,
                "promptTokens": defaults["promptTokens"],
                "genTokens": defaults["genTokens"],
                "cooldownSeconds": cooldown,
                "thermalGateCeilingC": ceiling,
                "thermalMaxWaitSeconds": defaults["thermalMaxWaitSeconds"],
                "orderPolicy": "counterbalanced: forward on even rounds, reversed on odd",
                "startThermal": start_thermal,
            },
            "candidates": [c.to_json() for c in candidates],
            "warmup": warmup,
            "records": records,
            "cooldowns": cooldowns,
            "rows": rows,
            "stockBaseline": {
                "candidateId": STOCK_CANDIDATE.id,
                "resolvedThreads": stock_row.get("resolvedThreads") if stock_row else None,
                "resolvedThreadsBatch": stock_row.get("resolvedThreadsBatch") if stock_row else None,
                "metrics": stock_row.get("metrics") if stock_row else None,
            },
            "scoring": ranking,
            "thermal": thermal,
            "orderDrift": drift,
            "gates": outcome["gates"],
            "winner": outcome["winner"],
            "recommendation": outcome["recommendation"],
            "verdict": outcome["verdict"],
            "verdictReason": outcome["reason"],
            "notes": notes,
        }
        report_path = write_report(Path(args.out_dir), report)
        export_requested_policy(args, report_path)

        if not args.dry_run:
            print_table(rows)
        print(f"\nverdict: {outcome['verdict']} — {outcome['reason']}")
        for name, value in outcome["gates"].items():
            mark = {True: "ok", False: "FAIL", None: "n/a"}[value]
            print(f"  gate {name:<24} {mark}")
        print_recommendation(outcome["recommendation"])

        if (
            outcome["verdict"] in ("autotuned", "stock-default-optimal")
            and not args.dry_run
            and identity_complete
        ):
            cache_store(
                Path(args.cache), key,
                {
                    "recordedAt": now_utc(),
                    "deviceFingerprint": fingerprint,
                    "device": device,
                    "modelId": model_entry.get("id"),
                    "modelSha256": model_sha,
                    "clusterSignature": topo.cluster_signature(),
                    "midrSignature": topo.midr_signature(),
                    "llamaCppCommit": next(
                        (r.get("llamaCppCommit") for r in records if r.get("llamaCppCommit")),
                        None,
                    ),
                    "profileIdentity": {**profile_identity, "sha256": profile_identity_sha},
                    "verdict": outcome["verdict"],
                    "recommendation": outcome["recommendation"],
                    "scoring": {k: ranking[k] for k in ("rule", "stabilityK", "tgWeight", "maxRelStddev")},
                },
            )
            print(f"cached profile under key {key} in {args.cache}")
        elif not args.dry_run:
            reason = (
                "exact profile identity was incomplete"
                if not identity_complete
                else "the sweep did not produce a trustworthy winner"
            )
            print(f"not cached: {reason}")

        if args.dry_run:
            print("\ndry run — no device commands were executed.")
            return 0
        return 0 if outcome["verdict"] in ("autotuned", "stock-default-optimal") else 2
    except AutotuneError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def print_recommendation(recommendation: dict[str, Any] | None) -> None:
    if not recommendation:
        return
    print(
        f"\nrecommended llama.cpp flags for this (device, model): "
        f"{recommendation.get('benchFlags')}"
    )
    if recommendation.get("cpuMask"):
        print(f"  (taskset equivalent: taskset {recommendation['cpuMask']} ...)")


def export_requested_policy(args: argparse.Namespace, report_path: Path) -> None:
    if not args.export_android_policy:
        return
    output = Path(args.export_android_policy)
    try:
        policy = export_android_policy.export_policy(report_path, output)
    except export_android_policy.PolicyExportError as error:
        raise AutotuneError(f"Android policy disabled: {error}") from error
    print(
        f"exported Android policy {policy['candidateId']} to {output} "
        f"from report {policy['sourceReportSha256'][:12]}"
    )


def write_report(out_dir: Path, report: dict[str, Any]) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{stamp}-autotune"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "autotune.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
