"""Device-free tests for strict autotune-report to Android-policy export."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("export_android_policy.py")
SPEC = importlib.util.spec_from_file_location("export_android_policy", MODULE_PATH)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


def valid_report():
    identity = {
        "schemaVersion": 1,
        "binarySha256": "1" * 64,
        "llamaCppSourceCommit": "178a6c44937154dc4c4eff0d166f4a044c4fceba",
        "modelSha256": "2" * 64,
        "contextSize": 512,
        "benchmarkShape": {
            "promptTokens": 64,
            "genTokens": 32,
            "repetitions": 2,
            "rounds": 3,
        },
        "workloadClass": "interactive-chat-decode-weighted",
        "scoring": {"tgWeight": 0.7},
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    identity["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "schemaVersion": 1,
        "tool": "autotune",
        "dryRun": False,
        "source": "sweep",
        "verdict": "autotuned",
        "gates": {name: True for name in exporter.REQUIRED_GATES},
        "profileIdentity": identity,
        "device": {
            "fingerprint": "3" * 64,
            "model": "24094RAD4I",
            "soc": "MT6855",
        },
        "model": {"sha256": "2" * 64},
        "recommendation": {
            "source": "sweep",
            "candidateId": "pp8-tg6",
            "threads": 6,
            "threadsBatch": 8,
            "baselineThreads": 8,
            "baselineThreadsBatch": 8,
            "cpuMask": None,
        },
    }


class ExportAndroidPolicyTests(unittest.TestCase):
    def _export(self, report):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        report_path = root / "autotune.json"
        output_path = root / "BundledPhasePolicy.kt"
        report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")
        return temp, report_path, output_path

    def test_valid_fresh_phase_pair_generates_enabled_policy(self):
        temp, report_path, output_path = self._export(valid_report())
        with temp:
            policy = exporter.export_policy(report_path, output_path)
            source = output_path.read_text(encoding="utf-8")
            self.assertEqual(policy["decodeThreads"], 6)
            self.assertEqual(policy["prefillThreads"], 8)
            self.assertEqual(policy["baselineDecodeThreads"], 8)
            self.assertIn("val value = PhasePolicy(", source)
            self.assertIn('deviceModel = "24094RAD4I"', source)
            self.assertIn('llamaCommit = "178a6c449371"', source)

    def test_failed_gate_overwrites_output_with_disabled_policy(self):
        report = valid_report()
        report["gates"]["thermallyStable"] = False
        temp, report_path, output_path = self._export(report)
        with temp:
            output_path.write_text("stale winner", encoding="utf-8")
            with self.assertRaises(exporter.PolicyExportError):
                exporter.export_policy(report_path, output_path)
            source = output_path.read_text(encoding="utf-8")
            self.assertIn("val value: PhasePolicy? = null", source)
            self.assertNotIn("stale winner", source)

    def test_cache_or_affinity_winner_cannot_be_packaged(self):
        for mutation in ("cache", "affinity"):
            with self.subTest(mutation=mutation):
                report = valid_report()
                if mutation == "cache":
                    report["source"] = "cache"
                else:
                    report["recommendation"]["cpuMask"] = "3f"
                with self.assertRaises(exporter.PolicyExportError):
                    exporter.policy_from_report(report, "4" * 64)

    def test_tampered_identity_cannot_be_packaged(self):
        report = valid_report()
        report["profileIdentity"]["contextSize"] = 1024
        with self.assertRaisesRegex(exporter.PolicyExportError, "identity hash"):
            exporter.policy_from_report(report, "4" * 64)

    def test_missing_report_disables_existing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "BundledPhasePolicy.kt"
            output.write_text("stale winner", encoding="utf-8")
            with self.assertRaises(exporter.PolicyExportError):
                exporter.export_policy(root / "missing.json", output)
            self.assertIn(
                "val value: PhasePolicy? = null",
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
