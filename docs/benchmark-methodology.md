# Benchmark methodology

Two measurement layers (see PLAN.md §2 Phase 1):

1. **llama-bench** (native, over ADB) — pp512 / tg128 / pg512+128, ≥5 reps, mean+stddev. Upstream-comparable numbers. Excludes tokenization/sampling time.
2. **In-app runner** (Phase 4) — model load, TTFT, p50/p95 inter-token latency, peak RSS, thermal transitions, battery delta, sustained throughput.

Rules:
- Never compare across different model/prompt/sampling/settings without labeling the difference.
- Fixed seed, committed prompts (`benchmarks/prompts/`), deterministic sampling for correctness runs.
- Cold vs warm labeled; sustained runs 10–15 min.
- Result JSON schema: PLAN.md Part II §9.2 (schemaVersion 1).
- Device prep checklist: docs/reproducibility.md.
