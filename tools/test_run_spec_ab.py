import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("run_spec_ab.py")
SPEC = importlib.util.spec_from_file_location("run_spec_ab", MODULE_PATH)
assert SPEC and SPEC.loader
mtp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mtp
SPEC.loader.exec_module(mtp)


class RunSpecAbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = mtp.load_json(mtp.DEFAULT_SUITE)
        cls.ngram_suite = mtp.load_json(
            mtp.REPO_ROOT / "benchmarks" / "suites" / "phase3-ngram.json"
        )
        _, cls.models = mtp.load_manifest()

    def test_repository_suite_validates(self):
        defaults = mtp.validate_suite(self.suite, self.models)
        self.assertEqual(defaults["repetitions"], 5)
        self.assertEqual(len(self.suite["prompts"]), 3)

    def test_server_commands_share_target_and_only_mtp_gets_draft(self):
        defaults = mtp.validate_suite(self.suite, self.models)
        target = self.models[defaults["targetModel"]]
        draft = self.models[defaults["draftModel"]]
        baseline = mtp.build_server_parts(defaults, target, draft, "baseline")
        speculative = mtp.build_server_parts(defaults, target, draft, "mtp")
        self.assertEqual(
            baseline[baseline.index("-m") + 1],
            speculative[speculative.index("-m") + 1],
        )
        self.assertNotIn("--model-draft", baseline)
        self.assertEqual(
            speculative[speculative.index("--spec-type") + 1], "draft-mtp"
        )
        self.assertTrue(
            speculative[speculative.index("--model-draft") + 1].endswith(
                "mtp-gemma-4-E2B-it.gguf"
            )
        )

    def test_ngram_suite_has_no_draft_and_uses_pinned_flags(self):
        defaults = mtp.validate_suite(self.ngram_suite, self.models)
        target = self.models[defaults["targetModel"]]
        baseline = mtp.build_server_parts(defaults, target, None, "baseline")
        speculative = mtp.build_server_parts(defaults, target, None, "ngram")
        self.assertEqual(
            baseline[baseline.index("-m") + 1],
            speculative[speculative.index("-m") + 1],
        )
        self.assertNotIn("--model-draft", speculative)
        self.assertEqual(
            speculative[speculative.index("--spec-type") + 1], "ngram-mod"
        )
        self.assertEqual(
            speculative[speculative.index("--spec-ngram-mod-n-match") + 1],
            "24",
        )
        self.assertEqual(
            speculative[speculative.index("--spec-ngram-mod-n-min") + 1],
            "4",
        )
        self.assertEqual(
            speculative[speculative.index("--spec-ngram-mod-n-max") + 1],
            "32",
        )

    def test_stream_event_parsers_cover_native_and_openai_shapes(self):
        self.assertEqual(mtp._event_text({"content": "a"}), "a")
        self.assertEqual(
            mtp._event_text({"choices": [{"delta": {"content": "b"}}]}), "b"
        )
        self.assertEqual(
            mtp._event_text(
                {
                    "choices": [
                        {
                            "delta": {
                                "reasoning_content": "think",
                                "content": "answer",
                            }
                        }
                    ]
                }
            ),
            "thinkanswer",
        )
        self.assertEqual(
            mtp._event_finish_reason({"choices": [{"finish_reason": "length"}]}),
            "length",
        )

    def test_prompt_hashes_are_stable_and_distinct(self):
        first = mtp.prompt_provenance(self.suite["prompts"])
        second = mtp.prompt_provenance(self.suite["prompts"])
        self.assertEqual(first, second)
        self.assertEqual(len({row["messagesCanonicalSha256"] for row in first}), 3)

    def test_llama_cpp_provenance_records_candidate_patch(self):
        state = mtp.llama_cpp_source_state()
        self.assertEqual(
            state["commit"], self.suite["defaults"]["llamaCppBaseCommit"]
        )
        self.assertTrue(state["dirty"])
        self.assertEqual(len(state["worktreeDiffSha256"]), 64)
        patch = mtp.REPO_ROOT / self.suite["defaults"]["candidatePatch"]
        self.assertTrue(mtp.candidate_patch_applied(patch))

    def test_comparison_requires_matching_outputs_and_active_speculation(self):
        runs = []
        for mode, speed, draft_n, accepted in (
            ("baseline", 10.0, 0, 0),
            ("mtp", 12.0, 10, 7),
        ):
            runs.append(
                {
                    "mode": mode,
                    "promptId": "p",
                    "repetition": 1,
                    "ok": True,
                    "content": "same",
                    "contentSha256": "hash",
                    "timeToFirstTokenMs": 100.0,
                    "endToEndMs": 1000.0,
                    "timings": {
                        "predicted_per_second": speed,
                        "draft_n": draft_n,
                        "draft_n_accepted": accepted,
                    },
                }
            )
        defaults = {
            "repetitions": 1,
            "minimumSpeedup": 1.03,
            "maxZramGrowthBytes": 0,
            "maxSwapFreeDropKb": 0,
        }
        sessions = [
            {"mode": "baseline", "ready": True},
            {
                "mode": "mtp",
                "ready": True,
                "memorySummary": {"zramGrowthBytes": 0, "swapFreeDropKb": 0},
            },
        ]
        result = mtp.compare(runs, sessions, defaults)
        self.assertEqual(result["verdict"], "pass")
        self.assertAlmostEqual(result["decodeSpeedup"], 1.2)
        self.assertAlmostEqual(result["modes"]["mtp"]["acceptanceRate"], 0.7)

        runs[0]["content"] = ""
        result = mtp.compare(runs, sessions, defaults)
        self.assertEqual(result["verdict"], "fail")
        self.assertFalse(result["gates"]["allOutputsNonEmpty"])

    def test_ngram_comparison_uses_ngram_mode_and_resource_gates(self):
        runs = []
        for mode, speed, drafts in (
            ("baseline", 14.0, 0),
            ("ngram", 15.0, 20),
        ):
            runs.append(
                {
                    "mode": mode,
                    "promptId": "p",
                    "repetition": 1,
                    "ok": True,
                    "content": "same",
                    "contentSha256": "hash",
                    "timeToFirstTokenMs": 100.0,
                    "endToEndMs": 1000.0,
                    "stateBefore": {"batteryTempC": 36.0},
                    "stateAfter": {"batteryTempC": 37.0},
                    "timings": {
                        "predicted_per_second": speed,
                        "draft_n": drafts,
                        "draft_n_accepted": drafts,
                    },
                }
            )
        defaults = {
            "speculativeMode": "ngram",
            "repetitions": 1,
            "minimumSpeedup": 1.03,
            "maxZramGrowthBytes": 0,
            "requireZramMetric": False,
            "maxSwapFreeDropKb": 65536,
            "maxSpeculativeRssIncreaseKb": 65536,
            "maxBatteryTempC": 43.0,
            "maxBatteryTempRiseC": 3.0,
        }
        sessions = [
            {
                "mode": "baseline",
                "ready": True,
                "stateBeforeLoad": {"batteryTempC": 36.0},
                "memorySummary": {
                    "maxProcessVmHwmKb": 800000,
                    "swapFreeDropKb": 0,
                },
            },
            {
                "mode": "ngram",
                "ready": True,
                "stateBeforeLoad": {"batteryTempC": 36.0},
                "memorySummary": {
                    "maxProcessVmHwmKb": 816384,
                    "swapFreeDropKb": 0,
                    "zramGrowthBytes": None,
                },
            },
        ]
        result = mtp.compare(runs, sessions, defaults)
        self.assertEqual(result["verdict"], "pass")
        self.assertIn("ngram", result["modes"])
        self.assertNotIn("mtp", result["modes"])
        self.assertEqual(result["pairedOutputs"][0]["ngramSha256"], "hash")
        self.assertTrue(result["gates"]["rssIncreaseWithinLimit"])


if __name__ == "__main__":
    unittest.main()
