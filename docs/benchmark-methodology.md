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

## Tooling (Phase 1)

- Suites live in `benchmarks/suites/*.json` (data-driven matrices); schema documented in
  `benchmarks/suites/README.md`. First suite: `phase1-baseline.json` (CPU thread sweep
  t=2/4/6/8 × pp512/tg128, a CPU pg512+128 case, and a Vulkan comparison group).
- `tools/run_suite.py <suite.json>` runs each case's `llama-bench` over adb with `-o json`,
  captures thermal/battery before and after, and writes one schemaVersion-1 result per case
  into `benchmarks/results/raw/<timestamp>-<suite>/` (git-ignored). The full llama-bench
  payload is preserved under `rawBench`. Flags: `--dry-run` (prints adb commands, no device
  needed), `--serial`, `--cooldown SECONDS` (default 120), `--device-snapshot`, `--ndk-version`.
- `tools/summarize_results.py <results-dir> [-o out.md]` renders a markdown table
  (case, backend, threads, pp/tg tok/s mean±std, thermal start→end).
- llama-bench (pinned `178a6c4`) has no seed or context-size flag; `seed`/`contextSize` are
  carried as metadata only. pp/tg workloads are synthetic — seed determinism applies to the
  `llama-cli` correctness runs, not here.
