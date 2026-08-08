#!/usr/bin/env python3
"""Tests for the checked-in real-generation evidence summarizer."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from summarize_real_generation import build_summary, nearest_rank


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "benchmarks/results/20260807-real-generation"
CONFIRMATION = EVIDENCE / "mobilespec-1786126555507.json"
SUSTAINED = [
    EVIDENCE / "mobilespec-1786128376471.json",
    EVIDENCE / "mobilespec-1786128714979.json",
    EVIDENCE / "mobilespec-1786129049935.json",
]
FINAL_TELEMETRY = (
    ROOT
    / "benchmarks/results/20260808-final-telemetry/mobilespec-1786188091323.json"
)


class RealGenerationSummaryTest(unittest.TestCase):
    def test_nearest_rank_uses_max_for_n15_p99(self) -> None:
        self.assertEqual(nearest_rank(list(range(1, 16)), 0.99), 15)

    def test_checked_in_bundle_builds_expected_summary(self) -> None:
        summary = build_summary(CONFIRMATION, SUSTAINED)
        sustained = summary["sustained"]
        self.assertEqual(sustained["sampleCountPerMode"], 15)
        self.assertTrue(sustained["correctnessMatched"])
        self.assertAlmostEqual(sustained["decodeMeanSpeedupRatio"], 2.073913, places=5)
        self.assertFalse(sustained["memory"]["processPeakRssAvailable"])
        self.assertFalse(sustained["memory"]["swapFreeAvailable"])

    def test_mixed_identity_is_rejected(self) -> None:
        changed = copy.deepcopy(json.loads(SUSTAINED[0].read_text(encoding="utf-8")))
        changed["traceability"]["modelSha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "traceability or benchmark config differs"):
                build_summary(CONFIRMATION, [path, *SUSTAINED[1:]])

    def test_final_telemetry_supplement_is_complete(self) -> None:
        result = json.loads(FINAL_TELEMETRY.read_text(encoding="utf-8"))
        self.assertTrue(result["correctnessMatched"])
        self.assertEqual(
            result["traceability"]["nativeLibrarySha256"],
            "1e50ca51c1228862f349232c6ffb0061e9edc6c10b682dc1e5e37998f96c3251",
        )
        self.assertEqual(
            result["traceability"]["appSourceSha256"],
            "239ed059e829ee68afbc14cf0fe853b5c6b1d47d31e242315ed7ce77e63534d6",
        )
        for run in [result["warmup"], *result["runs"]]:
            self.assertTrue(run["metrics"]["nativeTiming"])
            for point in ("before", "after"):
                telemetry = run["metrics"]["telemetry"][point]
                self.assertIsNotNone(telemetry["processPeakRssBytes"])
                self.assertIsNotNone(telemetry["swapFreeBytes"])


if __name__ == "__main__":
    unittest.main()
