"""Device-free unit tests for tools/autotune.py.

Everything here exercises pure logic: topology parsing, cluster classification,
candidate generation, hex affinity masks, the scoring rule, thermal/order-drift
detection, and cache key derivation. No adb call is made and no device is
required. Matches the unittest style of tools/test_run_spec_ab.py.

Run: python -m unittest discover -s tools -p 'test_*.py'
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("autotune.py")
SPEC = importlib.util.spec_from_file_location("autotune", MODULE_PATH)
assert SPEC and SPEC.loader
at = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = at
SPEC.loader.exec_module(at)


# ---------------------------------------------------------------------------
# Fixtures: three real-world Arm topologies.
# ---------------------------------------------------------------------------

def cpuinfo_text(parts_by_cpu: dict[int, str], implementer: str = "0x41") -> str:
    """Synthesize /proc/cpuinfo in the exact shape Android prints it."""
    blocks = []
    for cpu in sorted(parts_by_cpu):
        blocks.append(
            f"processor\t: {cpu}\n"
            "BogoMIPS\t: 26.00\n"
            "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp "
            "asimdhp cpuid asimdrdm lrcpc dcpop asimddp\n"
            f"CPU implementer\t: {implementer}\n"
            "CPU architecture: 8\n"
            "CPU variant\t: 0x0\n"
            f"CPU part\t: {parts_by_cpu[cpu]}\n"
            "CPU revision\t: 0\n"
        )
    return "\n".join(blocks) + "\n"


def cpufreq_text(freq_by_cpu: dict[int, int]) -> str:
    """Synthesize `grep . /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq`."""
    return "".join(
        f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq:{freq}\n"
        for cpu, freq in sorted(freq_by_cpu.items())
    )


# MediaTek Dimensity 7025 (MT6855): 6x Cortex-A55 on cpu0-5, 2x Cortex-A78 on
# cpu6-7. Big cores at the HIGH indices -- Phase 0 confirmed `taskset c0` pins
# the A78 pair -- so nothing may assume big cores are cpu0..n.
TOPO_2_6 = {
    "cpuinfo": cpuinfo_text({0: "0xd05", 1: "0xd05", 2: "0xd05", 3: "0xd05",
                             4: "0xd05", 5: "0xd05", 6: "0xd41", 7: "0xd41"}),
    "cpufreq": cpufreq_text({0: 2000000, 1: 2000000, 2: 2000000, 3: 2000000,
                             4: 2000000, 5: 2000000, 6: 2200000, 7: 2200000}),
    "present": "0-7\n",
    "meminfo": "MemTotal:        5860148 kB\nMemFree:          204512 kB\n"
               "MemAvailable:    1840236 kB\nSwapTotal:       3145724 kB\n"
               "SwapFree:        2500000 kB\n",
}

# Classic 4+4 (e.g. Snapdragon 6xx class): 4x A73 on cpu4-7, 4x A53 on cpu0-3.
TOPO_4_4 = {
    "cpuinfo": cpuinfo_text({0: "0xd03", 1: "0xd03", 2: "0xd03", 3: "0xd03",
                             4: "0xd09", 5: "0xd09", 6: "0xd09", 7: "0xd09"}),
    "cpufreq": cpufreq_text({0: 1800000, 1: 1800000, 2: 1800000, 3: 1800000,
                             4: 2200000, 5: 2200000, 6: 2200000, 7: 2200000}),
    "present": "0-7\n",
    "meminfo": "MemTotal:        3900000 kB\nMemAvailable:    1200000 kB\n",
}

# Tri-cluster 1+3+4 (modern flagship): 1x X4 prime, 3x A720 mid, 4x A520 little.
TOPO_1_3_4 = {
    "cpuinfo": cpuinfo_text({0: "0xd80", 1: "0xd80", 2: "0xd80", 3: "0xd80",
                             4: "0xd81", 5: "0xd81", 6: "0xd81", 7: "0xd82"}),
    "cpufreq": cpufreq_text({0: 1900000, 1: 1900000, 2: 1900000, 3: 1900000,
                             4: 2800000, 5: 2800000, 6: 2800000, 7: 3300000}),
    "present": "0-7\n",
    "meminfo": "MemTotal:       11800000 kB\nMemAvailable:    6400000 kB\n",
}


def topo(fixture: dict[str, str]) -> "at.Topology":
    return at.topology_from_sections(fixture, source="test")


def row(candidate_id, pp_samples, tg_samples, ok=True, threads=None, per_round=None):
    """Build an aggregated row the way aggregate_rounds would."""
    return {
        "id": candidate_id,
        "threads": threads,
        "cpuMask": None,
        "family": "unpinned",
        "rationale": "",
        "flags": "",
        "rounds": 1,
        "ok": ok,
        "resolvedThreads": threads,
        "metrics": {
            "ppTokensPerSec": at._pooled(pp_samples),
            "tgTokensPerSec": at._pooled(tg_samples),
        },
        "perRound": per_round or [],
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class ParsingTests(unittest.TestCase):
    def test_cpuinfo_parses_part_and_implementer_per_processor(self):
        parsed = at.parse_cpuinfo(TOPO_2_6["cpuinfo"])
        self.assertEqual(len(parsed), 8)
        self.assertEqual(parsed[0]["part"], "0xd05")
        self.assertEqual(parsed[7]["part"], "0xd41")
        self.assertEqual(parsed[7]["implementer"], "0x41")

    def test_cpuinfo_tolerates_missing_and_extra_fields(self):
        text = "processor\t: 0\nBogusField\t: 1\n\nprocessor\t: 1\nCPU part\t: 0xd05\n"
        parsed = at.parse_cpuinfo(text)
        self.assertEqual(set(parsed), {0, 1})
        self.assertNotIn("part", parsed[0])
        self.assertEqual(parsed[1]["part"], "0xd05")

    def test_cpufreq_parses_grep_output_and_skips_offline_cores(self):
        text = cpufreq_text({0: 1800000, 1: 1800000, 7: 3300000})
        self.assertEqual(at.parse_cpufreq(text), {0: 1800000, 1: 1800000, 7: 3300000})
        self.assertEqual(at.parse_cpufreq(""), {})
        self.assertEqual(at.parse_cpufreq("grep: no such file\n"), {})

    def test_present_and_meminfo(self):
        self.assertEqual(at.parse_present("0-7\n"), list(range(8)))
        self.assertEqual(at.parse_present("0-3,6-7"), [0, 1, 2, 3, 6, 7])
        mem = at.parse_meminfo(TOPO_2_6["meminfo"])
        self.assertEqual(mem["memTotalKb"], 5860148)
        self.assertEqual(mem["memAvailableKb"], 1840236)
        self.assertEqual(mem["swapFreeKb"], 2500000)

    def test_split_topology_output_sections(self):
        combined = (
            TOPO_2_6["cpuinfo"] + "__CPUFREQ__\n" + TOPO_2_6["cpufreq"]
            + "__PRESENT__\n" + TOPO_2_6["present"]
            + "__MEMINFO__\n" + TOPO_2_6["meminfo"]
        )
        sections = at.split_topology_output(combined)
        self.assertIn("processor", sections["cpuinfo"])
        self.assertIn("cpuinfo_max_freq", sections["cpufreq"])
        self.assertIn("MemAvailable", sections["meminfo"])
        self.assertEqual(at.parse_present(sections["present"]), list(range(8)))


# ---------------------------------------------------------------------------
# Cluster classification
# ---------------------------------------------------------------------------

class TopologyTests(unittest.TestCase):
    def test_2_6_classifies_high_index_a78_pair_as_big(self):
        t = topo(TOPO_2_6)
        self.assertEqual(t.coreCount, 8)
        self.assertEqual(len(t.clusters), 2)
        big, little = t.clusters
        self.assertEqual(big.tier, "big")
        self.assertEqual(big.cpuIds, (6, 7))
        self.assertEqual(big.label, "Cortex-A78")
        self.assertEqual(little.tier, "little")
        self.assertEqual(little.cpuIds, (0, 1, 2, 3, 4, 5))
        self.assertEqual(little.label, "Cortex-A55")
        self.assertEqual(t.midr_signature(), "2x0xd41+6x0xd05")
        self.assertEqual(t.confidence, "high")

    def test_4_4_classification(self):
        t = topo(TOPO_4_4)
        self.assertEqual([c.tier for c in t.clusters], ["big", "little"])
        self.assertEqual(t.clusters[0].cpuIds, (4, 5, 6, 7))
        self.assertEqual(t.clusters[0].label, "Cortex-A73")
        self.assertEqual(t.clusters[1].cpuIds, (0, 1, 2, 3))

    def test_tri_cluster_orders_prime_mid_little(self):
        t = topo(TOPO_1_3_4)
        self.assertEqual(len(t.clusters), 3)
        self.assertEqual([c.tier for c in t.clusters], ["big", "mid", "little"])
        self.assertEqual([c.size for c in t.clusters], [1, 3, 4])
        self.assertEqual(t.clusters[0].cpuIds, (7,))
        self.assertEqual(t.clusters[0].label, "Cortex-X4")
        self.assertEqual(t.clusters[1].label, "Cortex-A720")
        self.assertEqual(t.clusters[2].label, "Cortex-A520")

    def test_unknown_midr_degrades_gracefully_but_still_clusters_by_frequency(self):
        fixture = {
            "cpuinfo": cpuinfo_text({c: "0xfff" for c in range(8)}, implementer="0x99"),
            "cpufreq": cpufreq_text({c: (2000000 if c < 6 else 2600000) for c in range(8)}),
            "present": "0-7",
            "meminfo": "",
        }
        t = topo(fixture)
        self.assertEqual([c.tier for c in t.clusters], ["big", "little"])
        self.assertEqual(t.clusters[0].cpuIds, (6, 7))
        self.assertIn("unknown core", t.clusters[0].label)
        self.assertEqual(t.confidence, "high")  # cpufreq alone is enough

    def test_missing_cpufreq_falls_back_to_midr_and_lowers_confidence(self):
        fixture = {"cpuinfo": TOPO_2_6["cpuinfo"], "cpufreq": "", "present": "0-7", "meminfo": ""}
        t = topo(fixture)
        self.assertEqual(t.confidence, "medium")
        self.assertFalse(t.freqSignal)
        self.assertEqual(t.clusters[0].cpuIds, (6, 7))  # A78 still ranked above A55
        self.assertTrue(any("cpufreq" in note for note in t.notes))

    def test_no_signal_at_all_yields_one_low_confidence_cluster(self):
        fixture = {"cpuinfo": "processor\t: 0\nprocessor\t: 1\n", "cpufreq": "",
                   "present": "0-1", "meminfo": ""}
        t = topo(fixture)
        self.assertEqual(t.confidence, "low")
        self.assertEqual(len(t.clusters), 1)
        self.assertEqual(t.coreCount, 2)

    def test_same_frequency_different_midr_still_splits(self):
        fixture = {
            "cpuinfo": cpuinfo_text({0: "0xd05", 1: "0xd05", 2: "0xd41", 3: "0xd41"}),
            "cpufreq": cpufreq_text({c: 2000000 for c in range(4)}),
            "present": "0-3",
            "meminfo": "",
        }
        t = topo(fixture)
        self.assertEqual(len(t.clusters), 2)
        self.assertEqual(t.clusters[0].midrPart, "0xd41")


# ---------------------------------------------------------------------------
# Affinity masks
# ---------------------------------------------------------------------------

class HexMaskTests(unittest.TestCase):
    def test_masks_are_bare_lowercase_hex(self):
        # Phase 0 learning: toybox taskset wants a bare hex mask, `taskset c0`
        # pins the A78 pair (cpu6+cpu7) on the test device.
        self.assertEqual(at.hex_mask([6, 7]), "c0")
        self.assertEqual(at.hex_mask([0, 1, 2, 3, 4, 5]), "3f")
        self.assertEqual(at.hex_mask([0, 1, 2, 3]), "f")
        self.assertEqual(at.hex_mask([4, 5, 6, 7]), "f0")
        self.assertEqual(at.hex_mask([7]), "80")
        self.assertEqual(at.hex_mask(range(8)), "ff")
        self.assertEqual(at.hex_mask([]), "0")
        self.assertNotIn("0x", at.hex_mask([6, 7]))

    def test_masks_come_from_cluster_cpu_ids_not_from_position(self):
        t = topo(TOPO_2_6)
        self.assertEqual(at.hex_mask(t.clusters[0].cpuIds), "c0")   # big = cpu6,7
        self.assertEqual(at.hex_mask(t.clusters[1].cpuIds), "3f")   # little = cpu0-5

    def test_wide_masks_and_bad_input(self):
        self.assertEqual(at.hex_mask([64]), "10000000000000000")
        with self.assertRaises(at.AutotuneError):
            at.hex_mask([-1])


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

class CandidateTests(unittest.TestCase):
    def test_stock_default_is_always_first_and_carries_no_flags(self):
        for fixture in (TOPO_2_6, TOPO_4_4, TOPO_1_3_4):
            candidates = at.generate_candidates(topo(fixture))
            self.assertEqual(candidates[0].id, "stock-default")
            self.assertIsNone(candidates[0].threads)
            self.assertIsNone(candidates[0].threadsBatch)
            self.assertIsNone(candidates[0].cpuMask)
            self.assertEqual(candidates[0].flags(), "(stock defaults)")

    def test_2_6_candidates(self):
        candidates = at.generate_candidates(topo(TOPO_2_6))
        by_id = {c.id: c for c in candidates}
        self.assertIn("pin-big-t2", by_id)
        self.assertEqual(by_id["pin-big-t2"].cpuMask, "c0")
        self.assertIn("pin-little-t6", by_id)
        self.assertEqual(by_id["pin-little-t6"].cpuMask, "3f")
        unpinned = sorted(c.threads for c in candidates if c.family == "unpinned")
        # boundaries: 2 (big cluster), 6 (little cluster width), 8 (all), plus the
        # midpoint 4 of the wide 2->6 gap. t=4 matters here: Phase 1 measured it
        # *slower* than t=2, which is the non-monotonicity the tool exists to find.
        self.assertEqual(unpinned, [2, 4, 6, 8])
        self.assertTrue(all(c.cpuMask is None for c in candidates if c.family == "unpinned"))
        pair = by_id["pp8-tg6"]
        self.assertEqual((pair.threadsBatch, pair.threads), (8, 6))
        self.assertEqual(pair.flags(), "-t 6 -tb 8")
        self.assertIsNone(pair.cpuMask)

    def test_4_4_candidates_differ_from_2_6(self):
        candidates = at.generate_candidates(topo(TOPO_4_4))
        by_id = {c.id: c for c in candidates}
        self.assertEqual(by_id["pin-big-t4"].cpuMask, "f0")     # A73s at cpu4-7
        self.assertEqual(by_id["pin-little-t4"].cpuMask, "f")   # A53s at cpu0-3
        unpinned = sorted(c.threads for c in candidates if c.family == "unpinned")
        self.assertEqual(unpinned, [4, 6, 8])
        self.assertNotIn("t2", by_id)  # nothing here knows about a 2-big chip

    def test_tri_cluster_generates_a_prefix_ladder(self):
        candidates = at.generate_candidates(topo(TOPO_1_3_4))
        by_id = {c.id: c for c in candidates}
        self.assertEqual(by_id["pin-big-t1"].cpuMask, "80")          # X4 = cpu7
        self.assertEqual(by_id["pin-big+mid-t4"].cpuMask, "f0")      # X4 + 3x A720
        self.assertEqual(by_id["pin-little-t4"].cpuMask, "f")        # 4x A520
        unpinned = sorted(c.threads for c in candidates if c.family == "unpinned")
        self.assertEqual(unpinned, [1, 2, 4, 6, 8])

    def test_candidates_are_unique_and_bounded(self):
        for fixture in (TOPO_2_6, TOPO_4_4, TOPO_1_3_4):
            candidates = at.generate_candidates(topo(fixture))
            keys = [(c.threads, c.threadsBatch, c.cpuMask) for c in candidates]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertLessEqual(len(candidates), 16)
            capped = at.generate_candidates(topo(fixture), max_candidates=4)
            self.assertEqual(len(capped), 4)
            self.assertEqual(capped[0].id, "stock-default")

    def test_low_confidence_topology_proposes_no_affinity_masks(self):
        fixture = {"cpuinfo": "processor\t: 0\nprocessor\t: 1\nprocessor\t: 2\nprocessor\t: 3\n",
                   "cpufreq": "", "present": "0-3", "meminfo": ""}
        candidates = at.generate_candidates(topo(fixture))
        self.assertTrue(all(c.cpuMask is None for c in candidates))
        self.assertTrue(any(c.family == "unpinned" for c in candidates))

    def test_bench_command_omits_thread_flag_for_stock_and_adds_mask_otherwise(self):
        defaults = {"binDir": "/data/local/tmp/llama-edge",
                    "modelsDir": "/data/local/tmp/llama-edge/models",
                    "binary": "llama-bench", "promptTokens": 64, "genTokens": 32,
                    "contextSize": 512, "nGpuLayers": 0}
        _, stock = at.build_bench_cmd(at.STOCK_CANDIDATE, defaults, "M.gguf", 2)
        self.assertNotIn(" -t ", stock)
        self.assertNotIn(" -C ", stock)
        self.assertIn("-p 64", stock)
        self.assertIn("-n 32", stock)
        self.assertIn("-c 512", stock)
        self.assertIn("-o json", stock)
        pinned = at.generate_candidates(topo(TOPO_2_6))[1]
        _, shell = at.build_bench_cmd(pinned, defaults, "M.gguf", 2)
        self.assertIn(f"-t {pinned.threads}", shell)
        self.assertIn(f"-C {pinned.cpuMask}", shell)
        pair = next(c for c in at.generate_candidates(topo(TOPO_2_6)) if c.id == "pp8-tg6")
        _, shell = at.build_bench_cmd(pair, defaults, "M.gguf", 2)
        self.assertIn("-t 6", shell)
        self.assertIn("-tb 8", shell)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class ScoringTests(unittest.TestCase):
    def test_higher_mean_but_high_variance_loses(self):
        """The headline property of the rule.

        `fast-but-wild` has the better mean on both metrics; `steady` wins
        because mean - 1*stddev is worse for the wild one. Disqualification is
        switched off here so the ranking is decided by the score alone.
        """
        rows = [
            row("stock-default", [60.0, 60.0], [10.0, 10.0]),
            row("steady", [66.0, 66.2, 65.8], [12.0, 12.1, 11.9]),
            row("fast-but-wild", [84.0, 44.0, 82.0], [14.5, 9.5, 14.4]),
        ]
        ranking = at.score_rows(rows, stability_k=1.0, tg_weight=0.7, max_rel_stddev=10.0)
        self.assertEqual(ranking["bestId"], "steady")
        by_id = {r["id"]: r for r in rows}
        self.assertGreater(
            by_id["fast-but-wild"]["metrics"]["tgTokensPerSec"]["mean"],
            by_id["steady"]["metrics"]["tgTokensPerSec"]["mean"],
        )
        self.assertGreater(
            by_id["steady"]["score"]["combined"],
            by_id["fast-but-wild"]["score"]["combined"],
        )

    def test_t8_style_collapse_is_disqualified_outright(self):
        # The actual Phase 1 t=8 samples: highest peak, indefensible tail.
        rows = [
            row("stock-default", [60.0, 60.5, 59.5], [10.0, 10.1, 9.9]),
            row("t8", [74.8, 78.9, 78.1, 19.6, 41.7], [11.0, 11.2, 10.8, 3.0, 5.0]),
            row("t6", [68.0, 68.4, 68.2], [14.1, 14.2, 14.0]),
        ]
        ranking = at.score_rows(rows, max_rel_stddev=0.10)
        by_id = {r["id"]: r for r in rows}
        self.assertTrue(by_id["t8"]["score"]["disqualified"])
        self.assertFalse(by_id["t6"]["score"]["disqualified"])
        self.assertEqual(ranking["bestId"], "t6")
        self.assertIn("t8", [d["id"] for d in ranking["disqualified"]])

    def test_scores_are_ratios_against_the_stock_baseline(self):
        rows = [
            row("stock-default", [50.0], [10.0]),
            row("cand", [55.0], [11.0]),
        ]
        at.score_rows(rows, stability_k=0.0)
        score = rows[1]["score"]
        self.assertAlmostEqual(score["ratioVsBaseline"]["ppTokensPerSec"], 1.1)
        self.assertAlmostEqual(score["ratioVsBaseline"]["tgTokensPerSec"], 1.1)
        self.assertAlmostEqual(score["combined"], 1.1)
        self.assertAlmostEqual(rows[0]["score"]["combined"], 1.0)

    def test_tg_weight_controls_which_metric_decides(self):
        rows = [
            row("stock-default", [50.0], [10.0]),
            row("prefill-friendly", [70.0], [9.5]),
            row("decode-friendly", [45.0], [12.0]),
        ]
        decode_first = at.score_rows(rows, stability_k=0.0, tg_weight=0.9)
        self.assertEqual(decode_first["bestId"], "decode-friendly")
        prefill_first = at.score_rows(rows, stability_k=0.0, tg_weight=0.1)
        self.assertEqual(prefill_first["bestId"], "prefill-friendly")

    def test_failed_row_is_disqualified(self):
        rows = [row("stock-default", [50.0], [10.0]), row("broken", [], [], ok=False)]
        at.score_rows(rows)
        self.assertTrue(rows[1]["score"]["disqualified"])
        self.assertIsNone(rows[1]["score"]["combined"])

    def test_missing_baseline_falls_back_to_best_row(self):
        rows = [row("a", [50.0], [10.0]), row("b", [60.0], [12.0])]
        ranking = at.score_rows(rows, stability_k=0.0)
        self.assertEqual(ranking["normaliser"], "best-measured-row")
        self.assertEqual(ranking["bestId"], "b")


# ---------------------------------------------------------------------------
# Aggregation and run-order / thermal safeguards
# ---------------------------------------------------------------------------

def bench_record(candidate_id, round_index, position, pp, tg, temp_start, temp_end,
                 status="0 (NONE)"):
    return {
        "candidateId": candidate_id,
        "round": round_index,
        "position": position,
        "ok": True,
        "metrics": {
            "ppTokensPerSec": {"mean": sum(pp) / len(pp), "std": 0.0, "samples": pp},
            "tgTokensPerSec": {"mean": sum(tg) / len(tg), "std": 0.0, "samples": tg},
        },
        "thermalStart": {"batteryTempC": temp_start, "thermalStatus": status},
        "thermalEnd": {"batteryTempC": temp_end, "thermalStatus": status},
    }


class SweepHygieneTests(unittest.TestCase):
    def test_round_order_is_counterbalanced(self):
        candidates = at.generate_candidates(topo(TOPO_2_6))
        self.assertEqual(at.round_order(candidates, 0), candidates)
        self.assertEqual(at.round_order(candidates, 1), list(reversed(candidates)))
        self.assertEqual(at.round_order(candidates, 2), candidates)

    def test_aggregate_pools_samples_across_rounds_and_skips_warmup(self):
        candidates = at.generate_candidates(topo(TOPO_2_6))[:2]
        records = [
            bench_record(candidates[0].id, 1, 0, [50.0, 51.0], [10.0, 10.2], 30.0, 31.0),
            bench_record(candidates[0].id, 2, 1, [49.0, 50.0], [9.8, 10.0], 31.0, 32.0),
            {**bench_record(candidates[0].id, -1, 0, [1.1], [1.1], 30.0, 30.0),
             "scored": False},
            bench_record(candidates[1].id, 1, 1, [60.0], [12.0], 30.0, 31.0),
        ]
        rows = at.aggregate_rounds(candidates, records)
        first = rows[0]
        self.assertEqual(first["rounds"], 2)
        self.assertEqual(len(first["metrics"]["ppTokensPerSec"]["samples"]), 4)
        # 1.11 tok/s cold warm-up value must not appear anywhere in the pool.
        self.assertNotIn(1.1, first["metrics"]["tgTokensPerSec"]["samples"])
        self.assertGreater(first["metrics"]["ppTokensPerSec"]["mean"], 45.0)

    def test_thermal_assessment_flags_a_real_temperature_climb(self):
        records = [
            bench_record("a", 1, 0, [70.0], [10.0], 32.0, 34.0, status="0 (NONE)"),
            bench_record("b", 1, 1, [30.0], [4.0], 38.0, 40.0, status="2 (MODERATE)"),
        ]
        assessment = at.thermal_assessment(records, [], max_temp_rise_c=3.0)
        self.assertFalse(assessment["stable"])
        self.assertAlmostEqual(assessment["batteryTempRiseC"], 8.0)
        self.assertTrue(any("rose" in r for r in assessment["reasons"]))
        # Temperature did the work; status is recorded but not a reason.
        self.assertEqual(assessment["maxThermalStatus"], 2)
        self.assertIsNone(assessment["statusExceeded"])
        self.assertFalse(any("status" in r for r in assessment["reasons"]))

    def test_moderate_status_alone_does_not_make_a_quiet_sweep_unstable(self):
        """MT6855 reality: status 2 is the resting state while USB-charging.

        The 26/27 Jul evidence files record `Thermal Status: 2` at every
        telemetry point on runs that passed their own thermal gates with a
        0.2 C rise. Gating on status would make every sweep inconclusive.
        """
        records = [
            bench_record("a", 1, 0, [70.0], [10.0], 32.0, 32.1, status="2 (MODERATE)"),
            bench_record("b", 1, 1, [69.0], [10.1], 32.1, 32.2, status="2 (MODERATE)"),
        ]
        assessment = at.thermal_assessment(records, [], max_temp_rise_c=3.0)
        self.assertTrue(assessment["stable"])
        self.assertEqual(assessment["reasons"], [])
        self.assertEqual(assessment["maxThermalStatus"], 2)
        self.assertFalse(assessment["thermalStatusGated"])
        # ... but it is still surfaced as a warning, not silently dropped.
        self.assertTrue(any("thermal status" in w for w in assessment["warnings"]))

    def test_strict_status_gate_can_be_opted_into(self):
        records = [
            bench_record("a", 1, 0, [70.0], [10.0], 32.0, 32.1, status="2 (MODERATE)"),
        ]
        strict = at.thermal_assessment(
            records, [], max_temp_rise_c=3.0, max_thermal_status=1
        )
        self.assertFalse(strict["stable"])
        self.assertTrue(strict["statusExceeded"])
        self.assertTrue(strict["thermalStatusGated"])
        self.assertTrue(any("thermal status reached 2" in r for r in strict["reasons"]))

    def test_thermal_assessment_accepts_a_clean_sweep(self):
        records = [
            bench_record("a", 1, 0, [70.0], [10.0], 32.0, 33.0),
            bench_record("b", 1, 1, [69.0], [10.1], 32.5, 33.2),
        ]
        assessment = at.thermal_assessment(records, [], max_temp_rise_c=3.0)
        self.assertTrue(assessment["stable"])
        self.assertIsNone(assessment["statusExceeded"])
        self.assertEqual(assessment["reasons"], [])
        self.assertEqual(assessment["warnings"], [])

    def test_thermal_gate_timeout_is_recorded_as_a_reason(self):
        cooldowns = [{"beforeCandidateId": "t6", "timedOut": True, "polls": [
            {"batteryTempC": 41.0, "thermalStatus": "2 (MODERATE)"}]}]
        assessment = at.thermal_assessment([], cooldowns, max_temp_rise_c=3.0)
        self.assertFalse(assessment["stable"])
        self.assertTrue(any("timed out" in r for r in assessment["reasons"]))

    def test_drifted_sweep_is_flagged_instead_of_crowning_the_early_candidate(self):
        """The bug this whole design exists to prevent.

        Reproduces the measured failure: stock ran first at 76.30 pp512, t6 ran
        second at 30.70 pp512 after an 8 C battery temperature climb. On means
        alone the tool would 'discover' that stock is 2.5x faster. The
        temperature rise and the order-drift detector must both catch it --
        note that thermal *status* is not what saves us here.
        """
        candidates = [at.STOCK_CANDIDATE,
                      at.Candidate("t6", 6, None, "unpinned", "")]
        records = [
            bench_record("stock-default", 1, 0, [76.30], [10.52], 33.0, 36.0,
                         status="0 (NONE)"),
            bench_record("t6", 1, 1, [30.70], [4.10], 38.0, 41.0,
                         status="2 (MODERATE)"),
        ]
        rows = at.aggregate_rounds(candidates, records)
        ranking = at.score_rows(rows)
        thermal = at.thermal_assessment(records, [], max_temp_rise_c=3.0)
        drift = at.order_drift(rows)
        outcome = at.decide(rows, ranking, thermal, drift)

        self.assertEqual(ranking["bestId"], "stock-default")  # naive ranking agrees
        self.assertFalse(outcome["gates"]["thermallyStable"])
        self.assertFalse(outcome["gates"]["runOrderNeutral"])
        self.assertEqual(outcome["verdict"], "inconclusive")
        self.assertIsNone(outcome["winner"])
        self.assertEqual(outcome["recommendation"]["source"], "stock-default")

    def test_order_drift_detects_position_correlated_slowdown(self):
        candidates = [at.Candidate(f"c{i}", i + 2, None, "unpinned", "") for i in range(3)]
        # Every candidate is slower the later it runs, in both round orders.
        records = [
            bench_record("c0", 1, 0, [70.0], [10.0], 32.0, 33.0),
            bench_record("c1", 1, 1, [60.0], [8.5], 33.0, 34.0),
            bench_record("c2", 1, 2, [50.0], [7.0], 34.0, 35.0),
            bench_record("c2", 2, 0, [69.0], [9.9], 32.0, 33.0),
            bench_record("c1", 2, 1, [59.0], [8.4], 33.0, 34.0),
            bench_record("c0", 2, 2, [49.0], [6.9], 34.0, 35.0),
        ]
        rows = at.aggregate_rounds(candidates, records)
        drift = at.order_drift(rows)
        self.assertEqual(drift["roundsCompared"], 2)
        self.assertLess(drift["meanLastOverFirst"], 0.8)
        outcome = at.decide(rows, at.score_rows(rows),
                            at.thermal_assessment(records, [], max_temp_rise_c=10.0),
                            drift)
        self.assertFalse(outcome["gates"]["runOrderNeutral"])
        self.assertEqual(outcome["verdict"], "inconclusive")

    def test_clean_sweep_with_a_real_win_is_accepted(self):
        candidates = [at.STOCK_CANDIDATE, at.Candidate("t6", 6, None, "unpinned", "")]
        records = [
            bench_record("stock-default", 1, 0, [60.0, 60.4], [10.0, 10.1], 32.0, 32.6),
            bench_record("t6", 1, 1, [66.0, 66.3], [11.5, 11.6], 32.6, 33.1),
            bench_record("t6", 2, 0, [66.1, 66.2], [11.5, 11.4], 32.4, 33.0),
            bench_record("stock-default", 2, 1, [60.2, 60.1], [10.0, 9.9], 32.7, 33.2),
        ]
        rows = at.aggregate_rounds(candidates, records)
        ranking = at.score_rows(rows)
        thermal = at.thermal_assessment(records, [], max_temp_rise_c=3.0)
        outcome = at.decide(rows, ranking, thermal, at.order_drift(rows))
        self.assertTrue(outcome["gates"]["thermallyStable"])
        self.assertTrue(outcome["gates"]["beatsStockDefault"])
        self.assertEqual(outcome["verdict"], "autotuned")
        self.assertEqual(outcome["winner"]["id"], "t6")
        self.assertEqual(outcome["recommendation"]["benchFlags"], "-t 6")

    def test_no_candidate_beats_stock_keeps_stock(self):
        candidates = [at.STOCK_CANDIDATE, at.Candidate("t2", 2, None, "unpinned", "")]
        records = [
            bench_record("stock-default", 1, 0, [60.0, 60.1], [10.0, 10.0], 32.0, 32.5),
            bench_record("t2", 1, 1, [60.5, 60.4], [10.1, 10.1], 32.5, 33.0),
        ]
        rows = at.aggregate_rounds(candidates, records)
        outcome = at.decide(rows, at.score_rows(rows),
                            at.thermal_assessment(records, [], max_temp_rise_c=3.0),
                            at.order_drift(rows))
        self.assertEqual(outcome["verdict"], "stock-default-optimal")
        self.assertEqual(outcome["recommendation"]["benchFlags"], "(stock defaults)")

    def test_thermal_code_parsing(self):
        self.assertEqual(at.thermal_code({"thermalStatus": "0 (NONE)"}), 0)
        self.assertEqual(at.thermal_code({"thermalStatus": "2 (MODERATE)"}), 2)
        self.assertIsNone(at.thermal_code({"thermalStatus": None}))
        self.assertIsNone(at.thermal_code(None))

    def test_thermal_ok_gate_is_temperature_driven(self):
        self.assertEqual(
            at.thermal_ok({"thermalStatus": "0 (NONE)", "batteryTempC": 32.0}, 33.0),
            (True, []),
        )
        # Cool but MODERATE: proceed. This is the target device's normal state.
        self.assertEqual(
            at.thermal_ok({"thermalStatus": "2 (MODERATE)", "batteryTempC": 32.0}, 33.0),
            (True, []),
        )
        ok, reasons = at.thermal_ok(
            {"thermalStatus": "2 (MODERATE)", "batteryTempC": 39.0}, 33.0
        )
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 1)
        self.assertIn("batteryTempC", reasons[0])

    def test_thermal_ok_status_gate_when_explicitly_requested(self):
        ok, reasons = at.thermal_ok(
            {"thermalStatus": "2 (MODERATE)", "batteryTempC": 32.0}, 33.0, max_status=1
        )
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 1)
        self.assertIn("thermalStatus", reasons[0])
        ok, _ = at.thermal_ok(
            {"thermalStatus": "1 (LIGHT)", "batteryTempC": 32.0}, 33.0, max_status=1
        )
        self.assertTrue(ok)

    def test_wait_for_thermal_polls_until_cool_without_touching_a_device(self):
        snapshots = [
            {"batteryTempC": 40.0, "thermalStatus": "2 (MODERATE)", "capturedAt": "t0"},
            {"batteryTempC": 36.0, "thermalStatus": "1 (LIGHT)", "capturedAt": "t1"},
            {"batteryTempC": 32.5, "thermalStatus": "0 (NONE)", "capturedAt": "t2"},
        ]
        slept: list[float] = []
        original = at.run_suite.capture_thermal
        at.run_suite.capture_thermal = lambda serial, dry: snapshots.pop(0)
        try:
            record = at.wait_for_thermal(
                None, False, ceiling_c=33.0, cooldown_s=120, max_wait_s=300,
                poll_s=15, sleep=slept.append,
            )
        finally:
            at.run_suite.capture_thermal = original
        self.assertTrue(record["gateSatisfied"])
        self.assertEqual(len(record["polls"]), 3)
        self.assertEqual(slept, [120, 15, 15])
        self.assertEqual(record["waitedSeconds"], 150)

    def test_wait_for_thermal_times_out_and_reports_it(self):
        original = at.run_suite.capture_thermal
        at.run_suite.capture_thermal = lambda serial, dry: {
            "batteryTempC": 42.0, "thermalStatus": "2 (MODERATE)", "capturedAt": "t"
        }
        try:
            record = at.wait_for_thermal(
                None, False, ceiling_c=33.0, cooldown_s=0, max_wait_s=30,
                poll_s=15, sleep=lambda _s: None,
            )
        finally:
            at.run_suite.capture_thermal = original
        self.assertFalse(record["gateSatisfied"])
        self.assertTrue(record["timedOut"])


# ---------------------------------------------------------------------------
# Fingerprint, cache key, bundled profiles
# ---------------------------------------------------------------------------

class CacheAndProfileTests(unittest.TestCase):
    DEVICE = {"model": "24094RAD4I", "soc": "MT6855", "serial": "TEST-PHONE-SERIAL"}
    MODEL_SHA = "fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8"

    def test_fingerprint_is_stable_and_ignores_serial(self):
        t = topo(TOPO_2_6)
        first = at.device_fingerprint(self.DEVICE, t)
        second = at.device_fingerprint({**self.DEVICE, "serial": "OTHER"}, t)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_shareable_device_block_redacts_adb_serial(self):
        block = at.run_suite.device_block(
            "PRIVATE-SERIAL",
            {"model": "phone", "soc": "soc", "serial": "PRIVATE-SERIAL"},
            False,
        )

        self.assertEqual("<redacted>", block["serial"])

    def test_fingerprint_changes_with_soc_or_topology(self):
        t = topo(TOPO_2_6)
        base = at.device_fingerprint(self.DEVICE, t)
        self.assertNotEqual(base, at.device_fingerprint({**self.DEVICE, "soc": "SM8650"}, t))
        self.assertNotEqual(base, at.device_fingerprint(self.DEVICE, topo(TOPO_4_4)))
        self.assertNotEqual(base, at.device_fingerprint(self.DEVICE, topo(TOPO_1_3_4)))

    def test_cache_key_combines_device_and_model(self):
        fingerprint = at.device_fingerprint(self.DEVICE, topo(TOPO_2_6))
        key = at.cache_key(fingerprint, self.MODEL_SHA)
        self.assertEqual(key, f"{fingerprint[:16]}:{self.MODEL_SHA[:16]}")
        self.assertNotEqual(key, at.cache_key(fingerprint, "0" * 64))
        self.assertEqual(len(key.split(":")), 2)

    def test_cache_key_without_a_model_hash(self):
        self.assertTrue(at.cache_key("a" * 64, None).endswith(":unknown-model"))

    def test_profile_identity_changes_with_binary_shape_and_scoring(self):
        defaults = {
            "promptTokens": 64,
            "genTokens": 32,
            "contextSize": 512,
            "repetitions": 2,
            "rounds": 3,
            "workloadClass": "interactive-chat-decode-weighted",
            "scoring": {"tgWeight": 0.7},
        }
        binary = {"sha256": "a" * 64, "llamaCppSourceCommit": "178a6c4"}
        identity, digest = at.build_profile_identity(defaults, self.MODEL_SHA, binary)
        self.assertEqual(identity["contextSize"], 512)
        self.assertEqual(len(digest), 64)
        _, changed_binary = at.build_profile_identity(
            defaults, self.MODEL_SHA, {**binary, "sha256": "b" * 64}
        )
        _, changed_shape = at.build_profile_identity(
            {**defaults, "promptTokens": 128}, self.MODEL_SHA, binary
        )
        _, changed_score = at.build_profile_identity(
            {**defaults, "scoring": {"tgWeight": 0.5}}, self.MODEL_SHA, binary
        )
        self.assertEqual(len({digest, changed_binary, changed_shape, changed_score}), 4)
        self.assertEqual(len(at.cache_key("f" * 64, self.MODEL_SHA, digest).split(":")), 3)

    def test_cache_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            at.cache_store(path, "k1", {"recommendation": {"benchFlags": "-t 6"}})
            at.cache_store(path, "k2", {"recommendation": {"benchFlags": "-t 4"}})
            cache = at.load_json_file(path, {})
            self.assertEqual(
                at.cache_lookup(cache, "k1")["recommendation"]["benchFlags"], "-t 6"
            )
            self.assertEqual(len(cache["entries"]), 2)
            self.assertIsNone(at.cache_lookup(cache, "missing"))

    def test_bundled_profile_table_is_valid_and_seeded(self):
        table = json.loads(at.DEFAULT_PROFILES.read_text(encoding="utf-8"))
        self.assertEqual(table["schemaVersion"], 1)
        self.assertTrue(table["profiles"])
        entry = table["profiles"][0]
        self.assertEqual(entry["confidence"], "low")
        self.assertTrue(entry["caveats"])
        self.assertTrue(
            any("ONE DEVICE" in caveat.upper() for caveat in entry["caveats"])
        )
        self.assertTrue(
            any("DID NOT REPRODUCE" in caveat.upper() for caveat in entry["caveats"])
        )

    def test_seeded_profile_matches_the_test_device_but_only_as_a_hint(self):
        table = json.loads(at.DEFAULT_PROFILES.read_text(encoding="utf-8"))
        fields = at.profile_match_fields(self.DEVICE, topo(TOPO_2_6), self.MODEL_SHA)
        entry, notes = at.match_known_profile(table, fields, "irrelevant-key")
        self.assertIsNone(entry)          # low confidence -> sweep anyway
        self.assertTrue(notes)
        trusted, _ = at.match_known_profile(
            table, fields, "irrelevant-key", trust_low_confidence=True
        )
        self.assertIsNotNone(trusted)
        self.assertEqual(trusted["recommendation"]["threads"], 6)

    def test_seeded_profile_does_not_match_other_devices_or_models(self):
        table = json.loads(at.DEFAULT_PROFILES.read_text(encoding="utf-8"))
        other_chip = at.profile_match_fields(
            {"model": "X", "soc": "SM8650"}, topo(TOPO_1_3_4), self.MODEL_SHA
        )
        self.assertEqual(
            at.match_known_profile(table, other_chip, "k", trust_low_confidence=True),
            (None, []),
        )
        other_model = at.profile_match_fields(self.DEVICE, topo(TOPO_2_6), "0" * 64)
        self.assertEqual(
            at.match_known_profile(table, other_model, "k", trust_low_confidence=True),
            (None, []),
        )

    def test_generic_high_profile_is_a_hint_without_full_identity(self):
        table = {"profiles": [{"id": "any-8-core", "confidence": "high",
                               "match": {"coreCount": 8},
                               "recommendation": {"threads": 4}}]}
        fields = at.profile_match_fields(self.DEVICE, topo(TOPO_4_4), self.MODEL_SHA)
        entry, notes = at.match_known_profile(table, fields, "k")
        self.assertIsNone(entry)
        self.assertTrue(notes)
        trusted, _ = at.match_known_profile(
            table, fields, "k", trust_low_confidence=True
        )
        self.assertEqual(trusted["id"], "any-8-core")

    def test_string_matching_is_case_insensitive(self):
        identity = "c" * 64
        table = {"profiles": [{"id": "e", "confidence": "high",
                               "match": {"socModel": "mt6855", "profileIdentitySha256": identity},
                               "recommendation": {}}]}
        fields = at.profile_match_fields(
            self.DEVICE, topo(TOPO_2_6), self.MODEL_SHA, identity
        )
        self.assertIsNotNone(at.match_known_profile(table, fields, "k")[0])


class SuiteFileTests(unittest.TestCase):
    def test_bundled_suite_resolves_to_safe_defaults(self):
        suite = json.loads(at.DEFAULT_SUITE.read_text(encoding="utf-8"))
        args = at.build_arg_parser().parse_args([])
        defaults = at.resolve_defaults(suite, args)
        self.assertEqual(defaults["binary"], "llama-bench")
        self.assertEqual(defaults["contextSize"], 512)
        self.assertGreaterEqual(defaults["cooldownSeconds"], 60)  # never 0
        self.assertGreaterEqual(defaults["rounds"], 2)            # counterbalancing needs >= 2
        self.assertEqual(defaults["scoring"]["stabilityK"], 1.0)
        self.assertEqual(defaults["scoring"]["maxRelStddev"], 0.10)

    def test_cli_overrides_win(self):
        suite = json.loads(at.DEFAULT_SUITE.read_text(encoding="utf-8"))
        args = at.build_arg_parser().parse_args(
            ["--rounds", "5", "--cooldown", "30", "--tg-weight", "0.5", "--pp", "128"]
        )
        defaults = at.resolve_defaults(suite, args)
        self.assertEqual(defaults["rounds"], 5)
        self.assertEqual(defaults["cooldownSeconds"], 30)
        self.assertEqual(defaults["promptTokens"], 128)
        self.assertEqual(defaults["scoring"]["tgWeight"], 0.5)

    def test_invalid_options_are_rejected(self):
        suite = json.loads(at.DEFAULT_SUITE.read_text(encoding="utf-8"))
        with self.assertRaises(at.AutotuneError):
            at.resolve_defaults(
                suite, at.build_arg_parser().parse_args(["--tg-weight", "2.0"])
            )
        with self.assertRaises(at.AutotuneError):
            at.resolve_defaults(suite, at.build_arg_parser().parse_args(["--rounds", "0"]))
        with self.assertRaises(at.AutotuneError):
            at.resolve_defaults(
                suite, at.build_arg_parser().parse_args(["--context-size", "64"])
            )


class FakeDevice:
    """Stands in for adb so the whole tool can be driven with no hardware.

    Answers the four read-only shells the tool issues (topology, getprop,
    battery, thermal, meminfo) and synthesizes llama-bench JSON whose speed
    depends on the `-t`/`-C` in the command, so a full run has a knowable answer.
    """

    def __init__(self, tg_by_threads, pp_by_threads, default_threads=8, temp_c=32.0,
                 thermal_status=0, pp_by_threads_batch=None, binary_sha="a" * 64):
        self.tg_by_threads = tg_by_threads
        self.pp_by_threads = pp_by_threads
        self.default_threads = default_threads
        self.pp_by_threads_batch = pp_by_threads_batch or pp_by_threads
        self.binary_sha = binary_sha
        self.temp_c = temp_c
        self.thermal_status = thermal_status
        self.commands: list[str] = []

    def __call__(self, serial, args, dry_run, timeout=60):
        command = " ".join(args)
        self.commands.append(command)
        if "cpuinfo" in command and "__CPUFREQ__" in command:
            return (
                TOPO_2_6["cpuinfo"] + "__CPUFREQ__\n" + TOPO_2_6["cpufreq"]
                + "__PRESENT__\n" + TOPO_2_6["present"]
                + "__MEMINFO__\n" + TOPO_2_6["meminfo"]
            )
        if "getprop" in command:
            return {"ro.product.model": "24094RAD4I", "ro.soc.model": "MT6855",
                    "ro.build.version.release": "16"}.get(args[-1], "")
        if "dumpsys battery" in command:
            return f"  level: 88\n  temperature: {int(self.temp_c * 10)}\n  status: 3\n"
        if "Thermal Status" in command:
            return f"Thermal Status: {self.thermal_status}\n"
        if "MemAvailable" in command:
            return "MemAvailable:    1840236 kB\n"
        if "sha256sum" in command:
            return f"{self.binary_sha}  ./llama-bench\n" if self.binary_sha else ""
        if "llama-bench" in command:
            threads = self.default_threads
            match = __import__("re").search(r"-t (\d+)", command)
            if match:
                threads = int(match.group(1))
            threads_batch = threads
            batch_match = __import__("re").search(r"-tb (\d+)", command)
            if batch_match:
                threads_batch = int(batch_match.group(1))
            reps = int(__import__("re").search(r"-r (\d+)", command).group(1))
            context_size = int(__import__("re").search(r"-c (\d+)", command).group(1))
            tg, pp = self.tg_by_threads[threads], self.pp_by_threads_batch[threads_batch]
            return json.dumps([
                {"build_commit": "178a6c4", "n_ctx": context_size, "n_threads": threads,
                 "n_threads_batch": threads_batch, "n_prompt": 64,
                 "n_gen": 0, "avg_ts": pp, "stddev_ts": 0.2,
                 "samples_ts": [pp] * reps},
                {"build_commit": "178a6c4", "n_ctx": context_size, "n_threads": threads,
                 "n_threads_batch": threads_batch, "n_prompt": 0,
                 "n_gen": 32, "avg_ts": tg, "stddev_ts": 0.05,
                 "samples_ts": [tg] * reps},
            ])
        return ""


class EndToEndTests(unittest.TestCase):
    """Drive main() start to finish against FakeDevice. No adb, no device."""

    def _run(self, fake, extra_args=None):
        import tempfile

        original_at, original_rs = at.run_adb, at.run_suite.run_adb
        at.run_adb = fake
        at.run_suite.run_adb = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "cache.json"
                code = at.main([
                    "--serial", "FAKE", "--cooldown", "0", "--rounds", "2",
                    "--reps", "2", "--no-known-profiles",
                    "--cache", str(cache), "--out-dir", str(Path(tmp) / "out"),
                    *(extra_args or []),
                ])
                reports = sorted((Path(tmp) / "out").glob("*/autotune.json"))
                report = json.loads(reports[-1].read_text(encoding="utf-8"))
                cache_data = at.load_json_file(cache, {})
            return code, report, cache_data
        finally:
            at.run_adb, at.run_suite.run_adb = original_at, original_rs

    def test_full_run_finds_the_non_monotonic_optimum_and_caches_it(self):
        # A deliberately non-monotonic curve: t=4 worse than t=2, t=6 best,
        # t=8 (the stock default) mediocre -- the Phase 1 shape.
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
        )
        code, report, cache = self._run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["verdict"], "autotuned")
        self.assertEqual(report["recommendation"]["threads"], 6)
        self.assertEqual(report["stockBaseline"]["resolvedThreads"], 8)
        self.assertTrue(report["gates"]["thermallyStable"])
        self.assertTrue(report["gates"]["beatsStockDefault"])
        self.assertGreater(report["recommendation"]["improvementVsStock"], 0.1)
        # cached under (device fingerprint, model sha) for next time
        self.assertIn(report["cacheKey"], cache["entries"])
        self.assertEqual(
            cache["entries"][report["cacheKey"]]["recommendation"]["threads"], 6
        )
        # the warm-up invocation exists and is excluded from scoring
        self.assertFalse(report["warmup"]["scored"])
        self.assertEqual(len(report["rows"]), len(report["candidates"]))

    def test_full_run_can_select_a_real_phase_pair(self):
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 10.0, 4: 11.0, 6: 14.0, 8: 10.0},
            pp_by_threads={1: 25.0, 2: 40.0, 4: 55.0, 6: 65.0, 8: 80.0},
        )
        code, report, _ = self._run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["verdict"], "autotuned")
        self.assertEqual(report["recommendation"]["candidateId"], "pp8-tg6")
        self.assertEqual(report["recommendation"]["threads"], 6)
        self.assertEqual(report["recommendation"]["threadsBatch"], 8)
        self.assertEqual(report["recommendation"]["baselineThreads"], 8)
        self.assertEqual(report["recommendation"]["baselineThreadsBatch"], 8)
        self.assertEqual(report["recommendation"]["benchFlags"], "-t 6 -tb 8")

    def test_second_run_reuses_the_cache_without_benchmarking(self):
        import tempfile

        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
        )
        original_at, original_rs = at.run_adb, at.run_suite.run_adb
        at.run_adb = fake
        at.run_suite.run_adb = fake
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "cache.json"
                args = ["--serial", "FAKE", "--cooldown", "0", "--rounds", "2",
                        "--reps", "2", "--no-known-profiles", "--cache", str(cache),
                        "--out-dir", str(Path(tmp) / "out")]
                self.assertEqual(at.main(args), 0)
                benches_after_first = sum("llama-bench" in c and " -r " in c for c in fake.commands)
                fake.commands.clear()
                self.assertEqual(at.main(args), 0)
                self.assertEqual(
                    sum("llama-bench" in c and " -r " in c for c in fake.commands), 0
                )
                report = json.loads(
                    sorted((Path(tmp) / "out").glob("*/autotune.json"))[-1]
                    .read_text(encoding="utf-8")
                )
                # --force re-sweeps
                fake.commands.clear()
                self.assertEqual(at.main(args + ["--force"]), 0)
                self.assertGreater(
                    sum("llama-bench" in c and " -r " in c for c in fake.commands), 0
                )
        finally:
            at.run_adb, at.run_suite.run_adb = original_at, original_rs
        self.assertGreater(benches_after_first, 0)
        self.assertEqual(report["source"], "cache")
        self.assertEqual(report["recommendation"]["threads"], 6)

    def test_missing_binary_hash_never_reuses_or_stores_a_profile(self):
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
            binary_sha=None,
        )
        code, report, cache = self._run(fake)
        self.assertEqual(code, 0)
        self.assertFalse(cache.get("entries"))
        self.assertTrue(any("identity is incomplete" in note for note in report["notes"]))

    def test_full_run_keeps_stock_when_nothing_is_meaningfully_better(self):
        flat = {t: 10.0 for t in (1, 2, 4, 6, 8)}
        fake = FakeDevice(tg_by_threads=flat, pp_by_threads={t: 60.0 for t in flat})
        code, report, cache = self._run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["verdict"], "stock-default-optimal")
        self.assertIsNone(report["recommendation"]["threads"])

    def test_permanent_moderate_status_still_yields_a_usable_result(self):
        """Regression: the target device sits at Thermal Status 2 permanently.

        Constant status 2, flat temperature. Gating on status would return
        `inconclusive` on every sweep forever and never cache a profile, which
        is exactly the bug this default avoids. The run must complete, name a
        winner, cache it -- and still surface the status as a warning.
        """
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
            thermal_status=2,  # MODERATE for the whole sweep, temperature flat
        )
        code, report, cache = self._run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["verdict"], "autotuned")
        self.assertEqual(report["recommendation"]["threads"], 6)
        self.assertTrue(report["gates"]["thermallyStable"])
        self.assertIsNotNone(report["winner"])
        self.assertIn(report["cacheKey"], cache["entries"])
        # Recorded and warned about, never silently dropped.
        self.assertEqual(report["thermal"]["maxThermalStatus"], 2)
        self.assertFalse(report["thermal"]["thermalStatusGated"])
        self.assertTrue(report["thermal"]["warnings"])
        self.assertIn("MODERATE", report["thermal"]["statusAdvisory"])

    def test_strict_status_flag_restores_gating(self):
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
            thermal_status=2,
        )
        # --thermal-max-wait 0 so the cooldown gate gives up immediately instead
        # of idling for real; the point under test is what happens afterwards.
        code, report, cache = self._run(
            fake, ["--max-thermal-status", "1", "--thermal-max-wait", "0"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertFalse(report["gates"]["thermallyStable"])
        self.assertTrue(report["thermal"]["statusExceeded"])
        self.assertIsNone(report["winner"])
        self.assertEqual(cache, {})  # a gated-out sweep is never cached

    def test_temperature_climb_produces_no_winner(self):
        """Temperature, not status, is what disqualifies a sweep by default."""

        class HeatingDevice(FakeDevice):
            def __call__(self, serial, args, dry_run, timeout=60):
                if "dumpsys battery" in " ".join(args):
                    self.temp_c += 0.5   # ratchets up all sweep long
                return super().__call__(serial, args, dry_run, timeout)

        fake = HeatingDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
        )
        code, report, cache = self._run(fake, ["--thermal-max-wait", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertFalse(report["gates"]["thermallyStable"])
        self.assertGreater(report["thermal"]["batteryTempRiseC"], 3.0)
        self.assertEqual(cache, {})

    def test_no_adb_binary_is_ever_invoked(self):
        fake = FakeDevice(
            tg_by_threads={1: 6.0, 2: 12.8, 4: 11.2, 6: 14.2, 8: 9.5},
            pp_by_threads={1: 30.0, 2: 66.1, 4: 63.4, 6: 68.2, 8: 58.6},
        )
        self._run(fake)
        self.assertTrue(fake.commands)
        # Every command is a read: no push, no install, no setprop, no write.
        forbidden = ("push", "pull", "install", "setprop", "root", "remount",
                     "reboot", "> /", "chmod", "rm ")
        for command in fake.commands:
            for word in forbidden:
                self.assertNotIn(word, command, f"non-read adb command: {command}")


if __name__ == "__main__":
    unittest.main()
