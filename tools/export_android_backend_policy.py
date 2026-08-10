#!/usr/bin/env python3
"""Fail-closed exporter from an Android backend qualification report to Kotlin."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "app/src/main/java/com/manishm/mobilespec/BundledBackendPolicy.kt"
EXPECTED_WORKLOAD = "interactive-chat-decode-weighted"
EXPECTED_SCORING = "e2e-3pct-correctness-memory-thermal-stability"
REQUIRED_GATE_NAMES = {
    "baseline-preflight",
    "candidate-preflight",
    "required-operations",
    "preflight-correctness",
    "model-load",
    "preflight-thermal",
    "preflight-memory-swap",
    "discarded-warmup",
    "scored-repetitions",
    "counterbalanced-order",
    "candidate-identity",
    "native-timing",
    "scored-correctness",
    "ttft",
    "end-to-end-improvement",
    "stability",
    "thermal",
    "memory-swap",
}


class PolicyExportError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise PolicyExportError(f"{name} must be a 64-character SHA-256")
    int(value, 16)
    return value.lower()


def choose_record(report: dict[str, Any], requested: str | None) -> dict[str, Any]:
    qualified = []
    for record in report.get("records", []):
        evaluation = record.get("evaluation", {})
        candidate = record.get("candidate", {})
        label = f"{candidate.get('backend')}:{candidate.get('gpuLayers')}"
        gates = evaluation.get("gates", [])
        gate_names = [gate.get("name") for gate in gates]
        complete_gates = (
            len(gate_names) == len(REQUIRED_GATE_NAMES)
            and set(gate_names) == REQUIRED_GATE_NAMES
        )
        if (
            evaluation.get("verdict") == "QUALIFIED"
            and complete_gates
            and all(gate.get("status") == "PASS" for gate in gates)
        ):
            qualified.append((label, record))
    if requested is not None:
        matches = [record for label, record in qualified if label == requested]
        if len(matches) != 1:
            raise PolicyExportError(f"qualified candidate {requested!r} was not found exactly once")
        return matches[0]
    if not qualified:
        raise PolicyExportError("report contains no fully qualified backend candidate")
    return max(
        (record for _, record in qualified),
        key=lambda item: item["evaluation"].get("endToEndImprovement") or float("-inf"),
    )


def validate(report: dict[str, Any], record: dict[str, Any], report_sha: str) -> dict[str, Any]:
    if report.get("schemaVersion") != 1:
        raise PolicyExportError("unsupported qualification schemaVersion")
    trace = report.get("traceability", {})
    candidate = record.get("candidate", {})
    backend = candidate.get("backend")
    gpu_layers = candidate.get("gpuLayers")
    if backend == "VULKAN" and gpu_layers != -1:
        raise PolicyExportError("VULKAN candidate must request full offload (-1)")
    if backend == "HYBRID" and (not isinstance(gpu_layers, int) or gpu_layers <= 0):
        raise PolicyExportError("HYBRID candidate must request a positive layer count")
    if backend not in ("VULKAN", "HYBRID"):
        raise PolicyExportError("candidate backend must be VULKAN or HYBRID")

    evidence = record
    baseline_preflight = evidence.get("baselinePreflight", {})
    preflight = evidence.get("candidatePreflight", {})
    required_preflight_checks = (
        "modelLoadSucceeded",
        "nonEmptyGreedyOutput",
        "cancellationPassed",
        "reusePassed",
        "deviceAvailableAfter",
    )
    if not all(baseline_preflight.get(name) is True for name in required_preflight_checks):
        raise PolicyExportError("CPU preflight is incomplete or failed")
    if not all(preflight.get(name) is True for name in required_preflight_checks):
        raise PolicyExportError("candidate preflight is incomplete or failed")
    if preflight.get("requiredOperationsPassed") is not True:
        raise PolicyExportError("required operation evidence did not pass")
    baseline_preflight_hash = require_sha(
        baseline_preflight.get("greedyOutputSha256"), "CPU preflight output"
    )
    candidate_preflight_hash = require_sha(
        preflight.get("greedyOutputSha256"), "candidate preflight output"
    )
    if baseline_preflight_hash != candidate_preflight_hash:
        raise PolicyExportError("preflight CPU/candidate outputs do not match")
    warmup = evidence.get("discardedWarmup", {})
    if warmup.get("backend") != backend or warmup.get("gpuLayers") != gpu_layers:
        raise PolicyExportError("discarded warm-up does not match the candidate")
    pairs = evidence.get("pairs", [])
    if len(pairs) != 3 or [pair.get("repetition") for pair in pairs] != [1, 2, 3]:
        raise PolicyExportError("qualification must contain exactly three scored pairs")
    if any(
        pair.get("baseline", {}).get("outputSha256") !=
        pair.get("candidate", {}).get("outputSha256")
        for pair in pairs
    ):
        raise PolicyExportError("scored CPU/candidate outputs do not match")
    if any(
        pair.get("baseline", {}).get("backend") != "CPU"
        or pair.get("baseline", {}).get("gpuLayers") != 0
        or pair.get("candidate", {}).get("backend") != backend
        or pair.get("candidate", {}).get("gpuLayers") != gpu_layers
        or pair.get("baseline", {}).get("metrics", {}).get("nativeTiming") is not True
        or pair.get("candidate", {}).get("metrics", {}).get("nativeTiming") is not True
        for pair in pairs
    ):
        raise PolicyExportError("scored measurement identity or native timing is invalid")
    candidate_first = [pair.get("candidateFirst") for pair in pairs]
    if any(a == b for a, b in zip(candidate_first, candidate_first[1:])):
        raise PolicyExportError("scored pair order is not counterbalanced")
    if trace.get("workloadClass") != EXPECTED_WORKLOAD:
        raise PolicyExportError("unsupported workload class")
    if trace.get("scoringPolicy") != EXPECTED_SCORING:
        raise PolicyExportError("unsupported scoring policy")
    if trace.get("maxTokens") != 64 or trace.get("temperature") != 0.0 or trace.get("seed") != 42:
        raise PolicyExportError("unsupported qualification output shape")

    fields = {
        "backend": backend,
        "gpuLayers": gpu_layers,
        "deviceFingerprintSha256": require_sha(
            trace.get("deviceFingerprintSha256"), "deviceFingerprintSha256"
        ),
        "vulkanDeviceIdentitySha256": require_sha(
            trace.get("vulkanDeviceIdentitySha256"), "vulkanDeviceIdentitySha256"
        ),
        "nativeLibrarySha256": require_sha(
            trace.get("nativeLibrarySha256"), "nativeLibrarySha256"
        ),
        "sourceReportSha256": report_sha,
        "cpuPhasePolicyIdentitySha256": require_sha(
            trace.get("phasePolicyIdentitySha256"), "phasePolicyIdentitySha256"
        ),
        "deviceModel": trace.get("deviceModel"),
        "socModel": trace.get("socModel"),
        "modelSha256": require_sha(trace.get("modelSha256"), "modelSha256"),
        "llamaCommit": trace.get("llamaCommit"),
        "contextSize": trace.get("contextSize"),
        "qualificationPromptSha256": require_sha(
            trace.get("promptSha256"), "promptSha256"
        ),
        "qualificationMaxTokens": trace.get("maxTokens"),
        "qualificationTemperature": trace.get("temperature"),
        "qualificationSeed": trace.get("seed"),
        "workloadClass": trace.get("workloadClass"),
        "scoringPolicy": trace.get("scoringPolicy"),
    }
    if not isinstance(fields["deviceModel"], str) or not fields["deviceModel"].strip():
        raise PolicyExportError("deviceModel is missing")
    if not isinstance(fields["socModel"], str) or not fields["socModel"].strip():
        raise PolicyExportError("socModel is missing")
    if not isinstance(fields["llamaCommit"], str) or not fields["llamaCommit"].strip():
        raise PolicyExportError("llamaCommit is missing")
    if not isinstance(fields["contextSize"], int) or fields["contextSize"] <= 0:
        raise PolicyExportError("contextSize must be positive")
    if not isinstance(fields["qualificationMaxTokens"], int) or fields["qualificationMaxTokens"] <= 0:
        raise PolicyExportError("qualification maxTokens must be positive")

    identity_payload = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    fields["profileIdentitySha256"] = sha256_bytes(identity_payload.encode("utf-8"))
    return fields


def kotlin_string(value: str) -> str:
    return json.dumps(value)


def render(fields: dict[str, Any]) -> str:
    status = f"enabled: {fields['backend']} {fields['gpuLayers']} from {fields['sourceReportSha256'][:12]}"
    return f'''package com.manishm.mobilespec

import com.manishm.mobilespec.engine.Backend
import com.manishm.mobilespec.engine.BackendPolicy

/** Generated by tools/export_android_backend_policy.py from a fully gated report. */
internal object BundledBackendPolicy {{
    const val status = {kotlin_string(status)}
    val value = BackendPolicy(
        backend = Backend.{fields['backend']},
        gpuLayers = {fields['gpuLayers']},
        profileIdentitySha256 = {kotlin_string(fields['profileIdentitySha256'])},
        deviceFingerprintSha256 = {kotlin_string(fields['deviceFingerprintSha256'])},
        vulkanDeviceIdentitySha256 = {kotlin_string(fields['vulkanDeviceIdentitySha256'])},
        nativeLibrarySha256 = {kotlin_string(fields['nativeLibrarySha256'])},
        sourceReportSha256 = {kotlin_string(fields['sourceReportSha256'])},
        cpuPhasePolicyIdentitySha256 = {kotlin_string(fields['cpuPhasePolicyIdentitySha256'])},
        deviceModel = {kotlin_string(fields['deviceModel'])},
        socModel = {kotlin_string(fields['socModel'])},
        modelSha256 = {kotlin_string(fields['modelSha256'])},
        llamaCommit = {kotlin_string(fields['llamaCommit'])},
        contextSize = {fields['contextSize']},
        qualificationPromptSha256 = {kotlin_string(fields['qualificationPromptSha256'])},
        qualificationMaxTokens = {fields['qualificationMaxTokens']},
        qualificationTemperature = {float(fields['qualificationTemperature'])}f,
        qualificationSeed = {fields['qualificationSeed']}L,
        workloadClass = {kotlin_string(fields['workloadClass'])},
        scoringPolicy = {kotlin_string(fields['scoringPolicy'])},
    )
}}
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--candidate", help="candidate label, e.g. HYBRID:8")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="validate without writing Kotlin")
    args = parser.parse_args()

    report_bytes = args.report.read_bytes()
    report = json.loads(report_bytes)
    record = choose_record(report, args.candidate)
    fields = validate(report, record, sha256_bytes(report_bytes))
    if not args.check:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(fields), encoding="utf-8")
        print(args.output)
    print(f"qualified {fields['backend']}:{fields['gpuLayers']} {fields['profileIdentitySha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
