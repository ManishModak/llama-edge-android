import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_android_backend_policy as exporter


class BackendPolicyExporterTest(unittest.TestCase):
    def test_fully_qualified_report_exports_bound_policy(self):
        report = self.report()
        record = exporter.choose_record(report, None)

        fields = exporter.validate(report, record, "f" * 64)
        kotlin = exporter.render(fields)

        self.assertEqual("HYBRID", fields["backend"])
        self.assertEqual(8, fields["gpuLayers"])
        self.assertEqual(64, len(fields["profileIdentitySha256"]))
        self.assertIn("Backend.HYBRID", kotlin)
        self.assertIn("qualificationMaxTokens = 64", kotlin)

    def test_inconclusive_or_failed_gate_cannot_export(self):
        report = self.report()
        report["records"][0]["evaluation"]["gates"][0]["status"] = "INCONCLUSIVE"

        with self.assertRaisesRegex(exporter.PolicyExportError, "no fully qualified"):
            exporter.choose_record(report, None)

    def test_output_mismatch_cannot_export_even_if_verdict_is_forged(self):
        report = self.report()
        report["records"][0]["pairs"][1]["candidate"]["outputSha256"] = "e" * 64
        record = exporter.choose_record(report, None)

        with self.assertRaisesRegex(exporter.PolicyExportError, "outputs do not match"):
            exporter.validate(report, record, "f" * 64)

    def test_incomplete_gate_set_cannot_export(self):
        report = self.report()
        report["records"][0]["evaluation"]["gates"].pop()

        with self.assertRaisesRegex(exporter.PolicyExportError, "no fully qualified"):
            exporter.choose_record(report, None)

    @staticmethod
    def report():
        trace = {
            "appCommit": "app",
            "appSourceSha256": "1" * 64,
            "llamaCommit": "178a6c449371",
            "llamaSourceDiffSha256": "2" * 64,
            "nativeLibrarySha256": "3" * 64,
            "modelSha256": "4" * 64,
            "modelName": "model.gguf",
            "contextSize": 512,
            "phasePolicyIdentitySha256": "5" * 64,
            "deviceFingerprintSha256": "6" * 64,
            "deviceModel": "phone",
            "socModel": "soc",
            "vulkanDeviceIdentitySha256": "7" * 64,
            "promptSha256": "8" * 64,
            "maxTokens": 64,
            "temperature": 0.0,
            "seed": 42,
            "workloadClass": exporter.EXPECTED_WORKLOAD,
            "scoringPolicy": exporter.EXPECTED_SCORING,
        }
        pairs = [
            {
                "repetition": index + 1,
                "candidateFirst": index % 2 == 1,
                "baseline": {
                    "backend": "CPU",
                    "gpuLayers": 0,
                    "outputSha256": "a" * 64,
                    "metrics": {"nativeTiming": True},
                },
                "candidate": {
                    "backend": "HYBRID",
                    "gpuLayers": 8,
                    "outputSha256": "a" * 64,
                    "metrics": {"nativeTiming": True},
                },
            }
            for index in range(3)
        ]
        preflight = {
            "modelLoadSucceeded": True,
            "nonEmptyGreedyOutput": True,
            "greedyOutputSha256": "b" * 64,
            "cancellationPassed": True,
            "reusePassed": True,
            "deviceAvailableAfter": True,
        }
        return {
            "schemaVersion": 1,
            "traceability": trace,
            "records": [
                {
                    "candidate": {"backend": "HYBRID", "gpuLayers": 8},
                    "baselinePreflight": dict(preflight),
                    "candidatePreflight": {
                        **preflight,
                        "requiredOperationsPassed": True,
                    },
                    "discardedWarmup": {"backend": "HYBRID", "gpuLayers": 8},
                    "pairs": pairs,
                    "evaluation": {
                        "verdict": "QUALIFIED",
                        "endToEndImprovement": 0.12,
                        "gates": [
                            {"name": name, "status": "PASS", "detail": "ok"}
                            for name in sorted(exporter.REQUIRED_GATE_NAMES)
                        ],
                    },
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
