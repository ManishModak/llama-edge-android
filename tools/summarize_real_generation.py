#!/usr/bin/env python3
"""Validate MobileSpec real-generation evidence and emit summary JSON + PNG.

The confirmation bundle and every sustained-session bundle must use the same
model/build/policy/config identity, contain five runs per mode, and pass exact
output-hash correctness. The script refuses to chart a mixed or incomplete
bundle instead of silently combining incomparable measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


TRACE_KEYS = (
    "appCommit",
    "appSourceSha256",
    "llamaCommit",
    "llamaSourceDiffSha256",
    "nativeLibrarySha256",
    "modelSha256",
    "phasePolicyIdentitySha256",
    "deviceFingerprintSha256",
    "benchmarkBinarySha256",
    "sourceReportSha256",
    "baselineDecodeThreads",
    "baselinePrefillThreads",
    "decodeThreads",
    "prefillThreads",
)
MODES = ("BASELINE", "OPTIMIZED")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def describe(values: list[float]) -> dict[str, float | int]:
    mean = statistics.fmean(values)
    stddev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "mean": mean,
        "stddev": stddev,
        "coefficientOfVariation": stddev / mean if mean else 0.0,
        "min": min(values),
        "max": max(values),
        "p99NearestRank": nearest_rank(values, 0.99),
    }


def telemetry_points(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    runs = [bundle["warmup"], *bundle["runs"]]
    return [
        point
        for run in runs
        for point in (
            run["metrics"]["telemetry"].get("before"),
            run["metrics"]["telemetry"].get("after"),
        )
        if point is not None
    ]


def validate_bundle(path: Path, bundle: dict[str, Any]) -> None:
    if bundle.get("schemaVersion") != 1:
        raise ValueError(f"{path}: expected schemaVersion 1")
    if bundle.get("correctnessMatched") is not True:
        raise ValueError(f"{path}: exact output-hash correctness did not pass")
    if bundle.get("config", {}).get("repetitions") != 5:
        raise ValueError(f"{path}: expected exactly five repetitions")
    for mode in MODES:
        runs = [run for run in bundle.get("runs", []) if run.get("mode") == mode]
        if len(runs) != 5:
            raise ValueError(f"{path}: expected five {mode} runs, found {len(runs)}")
        if any(not run["metrics"].get("nativeTiming") for run in runs):
            raise ValueError(f"{path}: {mode} contains non-native timing")


def identity(bundle: dict[str, Any]) -> tuple[Any, ...]:
    traceability = bundle["traceability"]
    config = bundle["config"]
    return tuple(traceability.get(key) for key in TRACE_KEYS) + (
        config.get("prompt"),
        config.get("maxTokens"),
        config.get("temperature"),
        config.get("seed"),
    )


def mode_stats(bundles: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    runs = [run for bundle in bundles for run in bundle["runs"] if run["mode"] == mode]
    return {
        "decodeTokensPerSecond": describe(
            [run["metrics"]["decodeTokensPerSecond"] for run in runs]
        ),
        "timeToFirstTokenMs": describe(
            [run["metrics"]["timeToFirstTokenMs"] for run in runs]
        ),
        "endToEndDurationMs": describe(
            [run["metrics"]["totalDurationMs"] for run in runs]
        ),
        "outputSha256Values": sorted({run["outputSha256"] for run in runs}),
    }


def build_summary(
    confirmation_path: Path,
    sustained_paths: list[Path],
) -> dict[str, Any]:
    paths = [confirmation_path, *sustained_paths]
    bundles = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for path, bundle in zip(paths, bundles):
        validate_bundle(path, bundle)
    expected_identity = identity(bundles[0])
    for path, bundle in zip(paths[1:], bundles[1:]):
        if identity(bundle) != expected_identity:
            raise ValueError(f"{path}: traceability or benchmark config differs")

    confirmation = bundles[:1]
    sustained = bundles[1:]
    if not sustained:
        raise ValueError("at least one sustained bundle is required")
    sustained_points = [point for bundle in sustained for point in telemetry_points(bundle)]
    temperatures = [
        point["batteryTemperatureC"]
        for point in sustained_points
        if point.get("batteryTemperatureC") is not None
    ]
    available_memory = [point["availableMemoryBytes"] for point in sustained_points]
    sustained_start = min(point["timestampEpochMs"] for point in sustained_points)
    sustained_end = max(point["timestampEpochMs"] for point in sustained_points)

    confirmation_stats = {mode: mode_stats(confirmation, mode) for mode in MODES}
    sustained_stats = {mode: mode_stats(sustained, mode) for mode in MODES}
    baseline = sustained_stats["BASELINE"]
    optimized = sustained_stats["OPTIMIZED"]

    per_suite = []
    for path, bundle in zip(sustained_paths, sustained):
        row = {
            "file": path.name,
            "sha256": sha256(path),
            "resultId": bundle["id"],
            "correctnessMatched": bundle["correctnessMatched"],
        }
        for mode in MODES:
            row[mode] = mode_stats([bundle], mode)
        points = telemetry_points(bundle)
        row["thermal"] = {
            "startBatteryTemperatureC": points[0].get("batteryTemperatureC"),
            "endBatteryTemperatureC": points[-1].get("batteryTemperatureC"),
            "peakBatteryTemperatureC": max(
                point["batteryTemperatureC"]
                for point in points
                if point.get("batteryTemperatureC") is not None
            ),
            "thermalStatuses": sorted({point["thermalStatusName"] for point in points}),
        }
        per_suite.append(row)

    traceability = bundles[0]["traceability"]
    return {
        "schemaVersion": 1,
        "tool": "tools/summarize_real_generation.py",
        "scope": "Redmi Note 14 5G / MT6855 / frozen Llama 3.2 1B Q4_0 workload only",
        "traceability": traceability,
        "config": bundles[0]["config"],
        "sources": [
            {
                "role": "confirmation" if index == 0 else "sustained",
                "file": str(path),
                "sha256": sha256(path),
            }
            for index, path in enumerate(paths)
        ],
        "confirmation": {
            "sampleCountPerMode": 5,
            "modes": confirmation_stats,
            "correctnessMatched": True,
        },
        "sustained": {
            "suiteCount": len(sustained),
            "sampleCountPerMode": len(sustained) * 5,
            "sessionDurationMinutes": (sustained_end - sustained_start) / 60_000.0,
            "modes": sustained_stats,
            "decodeMeanSpeedupRatio": (
                optimized["decodeTokensPerSecond"]["mean"]
                / baseline["decodeTokensPerSecond"]["mean"]
            ),
            "ttftMeanImprovementRatio": 1.0
            - (
                optimized["timeToFirstTokenMs"]["mean"]
                / baseline["timeToFirstTokenMs"]["mean"]
            ),
            "endToEndMeanImprovementRatio": 1.0
            - (
                optimized["endToEndDurationMs"]["mean"]
                / baseline["endToEndDurationMs"]["mean"]
            ),
            "thermal": {
                "startBatteryTemperatureC": temperatures[0],
                "endBatteryTemperatureC": temperatures[-1],
                "peakBatteryTemperatureC": max(temperatures),
                "thermalStatuses": sorted(
                    {point["thermalStatusName"] for point in sustained_points}
                ),
            },
            "memory": {
                "minimumAvailableMemoryBytes": min(available_memory),
                "lowMemoryObserved": any(point["lowMemory"] for point in sustained_points),
                "processPeakRssAvailable": all(
                    point.get("processPeakRssBytes") is not None for point in sustained_points
                ),
                "swapFreeAvailable": all(
                    point.get("swapFreeBytes") is not None for point in sustained_points
                ),
            },
            "perSuite": per_suite,
            "correctnessMatched": True,
        },
        "claimScope": {
            "general": (
                "MobileSpec derives and measures phase-specific llama.cpp CPU policies from "
                "Arm Android topology and retains stock defaults unless all gates pass."
            ),
            "deviceSpecific": (
                "The measured pp8-tg2 policy is specific to this device, model, binary, "
                "context, and workload identity."
            ),
        },
    }


def write_chart(summary: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt

    sustained = summary["sustained"]
    modes = sustained["modes"]
    labels = ["Stock 8/8", "Phase-aware 8/2"]
    colors = ["#667085", "#2563eb"]
    throughput = [
        modes["BASELINE"]["decodeTokensPerSecond"],
        modes["OPTIMIZED"]["decodeTokensPerSecond"],
    ]
    ttft = [
        modes["BASELINE"]["timeToFirstTokenMs"],
        modes["OPTIMIZED"]["timeToFirstTokenMs"],
    ]
    suites = sustained["perSuite"]

    fig, axes = plt.subplots(1, 3, figsize=(16, 8.5))
    fig.patch.set_facecolor("#f8fafc")
    fig.subplots_adjust(left=0.055, right=0.985, bottom=0.16, top=0.82, wspace=0.24)
    fig.suptitle(
        "MobileSpec phase-aware CPU policy — Redmi Note 14 5G",
        fontsize=21,
        fontweight="bold",
    )

    ax = axes[0]
    bars = ax.bar(
        labels,
        [row["mean"] for row in throughput],
        yerr=[row["stddev"] for row in throughput],
        capsize=7,
        color=colors,
    )
    ax.set_title("Decode throughput\nmean ± sample SD, n=15/mode")
    ax.set_ylabel("tokens/second (higher is better)")
    ax.bar_label(bars, fmt="%.2f", padding=6, fontweight="bold")

    ax = axes[1]
    bars = ax.bar(labels, [row["mean"] for row in ttft], color=colors)
    ax.scatter(
        range(2),
        [row["p99NearestRank"] for row in ttft],
        marker="D",
        s=70,
        color="#dc2626",
        label="p99 (nearest rank)",
        zorder=3,
    )
    ax.set_title("Time to first token\nmean bars + p99 markers, n=15/mode")
    ax.set_ylabel("milliseconds (lower is better)")
    ax.bar_label(bars, fmt="%.0f", padding=6, fontweight="bold")
    ax.legend(loc="upper right")

    ax = axes[2]
    x_values = list(range(1, len(suites) + 1))
    for mode, label, color in zip(MODES, labels, colors):
        ax.plot(
            x_values,
            [suite[mode]["decodeTokensPerSecond"]["mean"] for suite in suites],
            marker="o",
            linewidth=2.5,
            markersize=8,
            label=label,
            color=color,
        )
    ax.set_xticks(x_values)
    ax.set_xlabel("consecutive A/B suite (no cooldown)")
    ax.set_ylabel("mean decode tokens/second")
    ax.set_title(
        f"Sustained session: {sustained['sessionDurationMinutes']:.2f} min\n"
        f"battery {sustained['thermal']['startBatteryTemperatureC']:.1f}→"
        f"{sustained['thermal']['endBatteryTemperatureC']:.1f} °C"
    )
    ax.legend(loc="center right")

    for ax in axes:
        ax.set_facecolor("white")
        ax.grid(axis="y", color="#e2e8f0", linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)

    speedup = sustained["decodeMeanSpeedupRatio"]
    fig.text(
        0.5,
        0.035,
        f"Exact output hashes matched in all 30 measured runs. "
        f"Phase-aware mean decode speedup: {speedup:.2f}×. "
        "Device/model/build/workload-specific result; not a universal phone claim.",
        ha="center",
        fontsize=11,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--sustained", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chart", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary(args.confirmation, args.sustained)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.chart is not None:
        write_chart(summary, args.chart)
    print(f"wrote {args.output}")
    if args.chart is not None:
        print(f"wrote {args.chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
