# Benchmark methodology

MobileSpec separates hardware diagnosis, no-regression microbenchmarks, and end-to-end user-visible
generation. A number is a submission result only when its raw samples and full provenance are
preserved.

## Evidence classes

| Class | Tool | Purpose | May support a final optimization claim? |
|---|---|---|---|
| Synthetic microbenchmark | `llama-bench` | Prefill/decode throughput, CPU thread sweep, backend comparison | Only as a no-regression or hardware-characterization result |
| End-to-end generation | `llama-server` for Phase 3; verified app engine for the final visible flow | TTFT, emitted-token throughput, speculation acceptance, correctness, memory, thermal behavior | Yes |
| Placement/profile evidence | `/proc` sampler, Perfetto, backend tests | Explain mechanism and reject inappropriate workstreams | Supports the explanation, not a user-visible speed claim by itself |
| Sustained run | End-to-end runner/app | Verify the gain survives heat, DVFS, and memory pressure | Required for the final claim |

At pinned llama.cpp commit `178a6c4`, `llama-bench` uses synthetic pp/tg token streams and excludes
tokenization/sampling time. It does not exercise the selected speculative end-to-end path.

## Non-negotiable comparison rules

Baseline and optimized runs must match on:

- target model file and SHA-256;
- target quantization;
- context size;
- fixed prompt bytes and prompt order;
- seed and sampling settings;
- generated-token limit and stop conditions;
- build toolchain and all code except the selected policy;
- thread count, affinity, backend, and GPU-layer configuration unless that field is the controlled
  variable;
- starting battery band, thermal status, temperature band, and background-device policy.

If any of these differ, label the comparison as exploratory and do not convert it into a percentage
improvement claim.

## Device controls

Before a measured session:

1. Enable airplane mode manually, disable Wi-Fi, close foreground applications, and set minimum
   brightness.
2. Start at 60–80% battery where practical. USB ADB may make battery-delta data unusable; disclose
   that rather than treating it as energy evidence.
3. Require Android thermal status `NONE` and record battery temperature, `MemAvailable`, and
   `SwapFree`.
4. Keep the screen state constant.
5. Record device model, SoC, Android build, CPU layout, Vulkan identity/driver when relevant, and
   the device serial in the private raw bundle.
6. Cool for the protocol's stated interval between cases.

After a session, clear any temporary stay-awake setting:

```bash
adb shell svc power stayon false
```

## Phase 1: official hardware baseline

The official suite is `benchmarks/suites/phase1-baseline.json`.

- Model: Llama 3.2 1B Instruct Q4_0
- Model SHA-256:
  `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`
- Project commit recorded in raw JSON:
  `4462c70587f9cdd6d00b67b5964cac060014c7a3`
- llama.cpp commit: `178a6c4`
- NDK: `28.2.13676358`
- Repetitions: 5 per case
- Warmup: enabled
- Cooldown: 120 seconds between cases
- CPU cases: 2/4/6/8 threads for `pp512` and `tg128`, plus 6-thread `pg512+128`
- Vulkan cases: `pp512`, `tg128`, and `pg512+128`, four CPU-side threads and `-ngl 99`
- Affinity: unpinned
- Total: 12 cases, all clean

The official bundle is
`benchmarks/results/raw/20260723-135809-phase1-baseline/`.

### Variance caveat

The first `cpu-t2-pp512` case includes a cold-start DVFS ramp, producing
`46.87 ± 10.73 tok/s`. A warm rerun had a compatible mean with lower spread. The official bundle
retains every original sample; it does not silently remove repetition 1.

For new suites, run a non-recorded warmup before the first measured case or define and publish a
first-repetition discard rule *before* collecting data. Never choose an outlier rule after seeing
which treatment it favors.

## Phase 2: bottleneck diagnosis

Phase 2 uses a different exploratory protocol and must not be mixed into the five-repetition
official baseline table without labeling it:

- `tg128` and `pp512`;
- three repetitions per affinity configuration;
- 30-second cooldowns;
- explicit `-C` masks and strict CPU placement where specified;
- `/proc/<pid>/task/*/stat` sampled at 20 Hz for residency and CPU time;
- Perfetto ftrace for scheduler context;
- fixed-seed CPU/Vulkan coherent-output comparison;
- filtered Vulkan backend-operator checks.

The Phase 2 evidence lives under
`benchmarks/results/profiles/20260723-105825/`.

`simpleperf` is not an available fallback on this device. The kernel refuses `perf_event_open` for
hardware and software events even with `perf_event_paranoid=-1`.

## Phase 3: native MTP feasibility gate

The native-MTP comparison must be end-to-end. Do not use a `llama-bench` delta as a substitute.
The implemented Phase 3 path is `llama-server` streaming over an ADB-forwarded local HTTP port.

### Controlled variable

Use the same compact Gemma target file for both arms:

- **Baseline:** target model, MTP disabled.
- **Candidate:** identical target model, identical settings, MTP enabled with its matching head.

Only MTP may differ. Record exact target and MTP-head SHA-256 values. A smaller quantization is
allowed only if both arms use that same target file.

### Load gate

Before collecting performance repetitions, require:

- target plus MTP head loads at `-c 512`, text-only;
- no OOM or process kill;
- no material ZRAM growth during load/generation;
- sufficient post-load `MemAvailable` for the measured run;
- peak RSS captured;
- coherent output from both arms;
- target verification remains active.

The feasibility suite predeclares zero permitted `SwapFree` drop and zero permitted ZRAM growth.
The measured native-MTP run failed the no-swap gate; ZRAM telemetry was unavailable, so that gate
remained inconclusive.

### Prompt strata

The `phase3-mtp` suite now fixes three prompts in its committed JSON:

1. a repetitive sequence;
2. a Python code continuation;
3. an ordinary explanatory chat prompt.

The output artifact preserves each complete request payload and the exact project commit. The
runner's final schema also records the suite-file and canonical per-prompt SHA-256 values. Target
token counts still need to come from the measured run; do not infer them from characters or a
different tokenizer.

### Per-repetition measurements

Record:

- prompt ID and SHA-256;
- wall-clock start and end;
- target model and optional MTP-head hashes;
- complete executable arguments or app configuration;
- prompt token count and emitted token count;
- TTFT;
- emitted tokens per second after first token;
- total end-to-end tokens per second;
- proposed and accepted draft tokens, acceptance rate, and accepted tokens per target pass;
- peak RSS, `MemAvailable` before/after, `SwapFree` before/after;
- battery level/temperature and thermal status before/after;
- output text or its artifact hash;
- exit status and stderr;
- warm/cold label.

Use at least five measured repetitions per prompt and mode. Interleave or counterbalance
baseline/optimized order so DVFS drift does not always favor one arm.

### Native-MTP feasibility result

The initial feasibility run intentionally used one repetition per prompt to avoid spending a
five-repetition session on a candidate that might fail basic load, correctness, memory, or speed
gates.

- Both modes loaded and all six requests completed.
- All three greedy output pairs matched exactly.
- Native MTP proposed 605 tokens and accepted 226 (37.355%).
- Baseline averaged 4.91956 tok/s across the three prompts; MTP averaged 4.82293 tok/s
  (**0.98036×**).
- `SwapFree` dropped by 450,620 kB in baseline mode and 150,028 kB in MTP mode.
- ZRAM metrics were unavailable.
- Both sessions began at thermal status 2, so this is not a clean-device promotion run.

The predeclared minimum speedup was 1.03× and no swap use was allowed. Verdict: **fail**. A
five-repetition native-MTP confirmation is not warranted. Evidence:
`benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json`, SHA-256
`81e92389437bfe9b02ab7c0b9d5dd41a29f5308bfbbd4711692b4ff114e58932`.

### Correctness gate

A candidate passes only if:

- each accepted speculative token is verified by the target path;
- deterministic fixed-seed checks produce the expected target-equivalent sequence when the
  executable supports that contract;
- ordinary prompts remain coherent under manual review;
- malformed output, premature stops, and error logs are absent;
- a perplexity spot-check passes when the selected executable exposes a comparable path.

Exact text identity is not required for a stochastic sampling configuration unless that contract
is declared in advance. Target verification, stop behavior, and coherent output are always
required.

## Adaptive policy evaluation

Implement an adaptive policy only after its underlying speculation mechanism shows a real
opportunity. Native MTP did not; the next candidate is the predeclared zero-weight n-gram fallback.

The policy evaluation must add:

- the feature values used to enable/disable speculation;
- the decision made for each request;
- the reason or threshold;
- false-positive cases where enabling speculation loses time;
- fallback behavior when the head is absent, incompatible, or memory pressure is too high.

Compare the frozen adaptive build to the same build with the policy disabled. A controller that
merely avoids a known-slower path can be reported as a routing win, but it must not be presented as
a raw decoder speedup.

## N-gram fallback

Native MTP failed its feasibility gate. The active in-workstream fallback is zero-weight
n-gram/prompt-lookup speculation on the existing verified Llama model.

Use the same end-to-end protocol and prompt strata. Report acceptance separately by stratum; a win
on repetitive text must not be generalized to ordinary chat. The fallback is not accepted merely
because it allocates no second weight set.

## Sustained-run protocol

The final candidate needs a 10–15 minute baseline and optimized run:

- identical repeating prompt schedule;
- throughput and TTFT recorded per request or fixed time window;
- temperature, thermal state, frequency information if available, RSS, `MemAvailable`, and
  `SwapFree` sampled throughout;
- mode order reversed in a second session if time permits;
- device cooled to the same starting band before each arm.

Report both initial and steady-state throughput. If a thermal transition occurs in only one arm,
report the transition and do not collapse the run into an unqualified mean.

### Final phase-aware result

The accepted device-specific candidate is stock prefill/decode `8/8` versus phase-aware `8/2`,
using one frozen Llama 3.2 1B Q4_0 model, context 512, binary, policy identity, prompt, seed, and
sampling configuration. The three-round synthetic discovery report passed every promotion gate;
the final claim is supported by real app generation rather than by the synthetic delta alone.

The confirmation bundle contains five runs/mode. The sustained evidence contains three consecutive
five-run/mode suites (15 samples/mode) spanning 14.37 minutes. Across those sustained samples,
decode throughput was `5.3897 +/- 0.8677 tok/s` baseline and `11.1777 +/- 0.1908 tok/s` optimized
(`2.0739x`); mean end-to-end duration fell from 25,244.9 ms to 12,000.5 ms. Exact output hashes
matched for every pair. Battery temperature rose from 35.5 C to 38.7 C and no low-memory signal was
observed. The older artifacts do not contain VmHWM or SwapFree. The separate final supplement
`benchmarks/results/20260808-final-telemetry/mobilespec-1786188091323.json` records a
1,760,477,184-byte process VmHWM and complete SwapFree samples. SwapFree recovered by 66,021,376
bytes across its ten scored runs after falling during the discarded warm-up; this system-wide
counter is disclosed without attributing that change to either policy.

`tools/summarize_real_generation.py` validates bundle identity and produces the checked-in summary
and chart. Its `p99NearestRank` is the nearest-rank order statistic; for `N=15`, p99 is therefore
the maximum sample and must not be interpreted as a population-tail estimate.

## Statistics and reporting

- Preserve all raw samples.
- Report `N`, arithmetic mean, standard deviation, and individual samples for small-N runs.
- Add median and p50/p95 inter-token latency for app/end-to-end results when the runner captures
  per-token timestamps.
- Report absolute and relative differences.
- Keep exploratory and confirmatory runs in separate bundles.
- Name failed/aborted cases and explain why they are excluded.
- Do not pool different prompts, models, quantizations, or thermal regimes into one speedup unless
  the aggregation rule and per-stratum results are shown.

Relative improvement:

```text
100 × (optimized_mean - baseline_mean) / baseline_mean
```

The final sentence must name device, driver when relevant, model, quantization, workload, metric,
and repetition count.

## Acceptance gates

| Gate | Pass condition |
|---|---|
| Provenance | Exact project/submodule commits, model hashes, commands, prompts, and raw samples exist |
| Isolation | Baseline and optimized differ only in the intended policy |
| Performance | Repeatable end-to-end improvement or demonstrable avoidance of a slower path |
| Correctness | Target verification and declared deterministic/coherence checks pass |
| Memory | No OOM or disallowed swap/ZRAM growth; peak RSS recorded |
| Thermal | Gain survives sustained load or degradation is disclosed |
| App | Same engine/config is used by the visible demo and evidence runner |

If any row is missing evidence, the final optimization claim is blocked.

## Final claim template

Do not fill this from memory or an exploratory run:

> On **[device and SoC]** running **[Android/driver]**, with
> **[model file, quantization, SHA-256 prefix]** at **[context]**, MobileSpec's
> **[exact policy]** changed **[metric]** from **[baseline mean ± std]** to
> **[optimized mean ± std]** across **[N]** repetitions of **[prompt/workload]**
> (**[relative change]%**), while **[correctness contract]** passed,
> **[peak RSS/swap outcome]**, and **[sustained-run outcome]**.
