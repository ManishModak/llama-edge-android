# Benchmark suites

Data-driven `llama-bench` matrices consumed by `tools/run_suite.py`. One JSON file
per suite; the runner produces one schemaVersion-1 result JSON per case under
`benchmarks/results/raw/<timestamp>-<suite>/` (that path is git-ignored).

## Suite schema (schemaVersion 1)

```jsonc
{
  "schemaVersion": 1,
  "suite": "phase1-baseline",          // matches the filename stem; used in the results dir name
  "description": "…",
  "defaults": { … },                    // per-case fields fall back to these
  "cases": [ { … }, … ]
}
```

### `defaults` (all optional; supply per-case overrides as needed)

| Field | Meaning | Maps to |
|---|---|---|
| `binDir` | On-device dir holding the binaries | `cd <binDir>` + `LD_LIBRARY_PATH` |
| `modelsDir` | On-device dir holding GGUF models | model path prefix |
| `binary` | Binary name inside `binDir` | `./<binary>` |
| `model` | Model **id** from `models/manifest.json` | resolved to `-m <modelsDir>/<file>` |
| `repetitions` | Reps per test | `-r` |
| `contextSize` | Explicit context allocation in patched llama-bench | `-c`; resolved `n_ctx` verified |
| `warmup` | `false` adds `--no-warmup` | `--no-warmup` |
| `seed` | Recorded metadata only | result `run.seed` |

### `cases[]`

| Field | Required | Meaning | Maps to |
|---|---|---|---|
| `id` | yes | Unique; becomes the result filename | — |
| `backend` | yes | `cpu` or `vulkan` (records `buildVariant`) | — |
| `threads` | yes | Thread count | `-t` |
| `nGpuLayers` | yes | Layers offloaded; `0` = CPU, `99` = all on GPU | `-ngl` |
| `test` | yes | `{ "type": "pp"\|"tg"\|"pg", "promptTokens": N, "genTokens": M }` | see below |
| `binary` | no | Override `defaults.binary` | `./<binary>` |
| `binDir` | no | Override `defaults.binDir` | — |
| `model` | no | Override `defaults.model` | `-m` |
| `repetitions` | no | Override `defaults.repetitions` | `-r` |
| `cpuMask` | no | Hex core mask for big.LITTLE pinning | `-C` |
| `note` | no | Free-text; ignored by the runner | — |

### `test.type` → llama-bench flags

- `pp` (prompt processing): `-p <promptTokens> -n 0`
- `tg` (text generation): `-p 0 -n <genTokens>`
- `pg` (combined prefill+decode): `-p 0 -n 0 -pg <promptTokens>,<genTokens>`

`-p 0 -n 0` on the `pg` case suppresses llama-bench's implicit default `pp512`/`tg128`
tests so only the combined test runs.

## Notes / open items

- **Seed**: `llama-bench` (pinned commit `178a6c4`) exposes no seed flag — its
  pp/tg workloads are synthetic token streams, so `seed` is metadata only. Seed
  matters for the deterministic `llama-cli` correctness runs, not here.
- **Context size**: upstream has no context flag at this commit. Local patch `0002` adds
  `-c/--ctx-size` and reports resolved `n_ctx`; the Phase 3B tuner requires the reported value to
  match the suite's 512-token APK context.
- **Vulkan binary name**: the suite assumes a Vulkan build pushed as
  `llama-bench-vulkan`. If your build is named differently or lives elsewhere,
  override `binary`/`binDir` on those cases. Until that binary exists, run the
  CPU half only with `python tools/run_suite.py <suite.json> --only-backend cpu`
  (the flag is repeatable and filters on each case's `backend` field).
- **big.LITTLE pinning**: the Dimensity 7025 is 2×A78 + 6×A55. The `t2` case only
  *intends* A78-only; real pinning needs `cpuMask` once the core index layout is
  confirmed on-device.

## Phase 3B core-policy autotuner

`autotune.json` is consumed by `tools/autotune.py`, not `run_suite.py`, and it
deliberately has **no `cases` array**: the candidate configurations are derived
from the device's CPU topology at run time. Encoding them here would be shipping
the constant instead of the method (docs/PLAN.md §1 rule 6). The file carries
only the invocation defaults and the scoring policy.

```sh
python tools/autotune.py --dry-run              # preview, needs no device
python tools/autotune.py --print-topology       # detect cores, print, exit
python tools/autotune.py                        # measure and cache a profile
python tools/autotune.py --force --rounds 5     # ignore the cache, re-sweep
python tools/autotune.py --force --export-android-policy  # fresh sweep + strict APK policy export
```

Resolution order is **cache → bundled profile → sweep**:

1. `benchmarks/results/autotune-cache.json` (git-ignored), keyed by
   `<deviceFingerprint16>:<modelSha16>:<profileIdentity16>`. The identity covers
   the exact benchmark binary, llama.cpp source commit, context/benchmark shape,
   workload class, and scoring policy, so stale profiles trigger a new sweep.
2. `benchmarks/profiles/known-profiles.json` (checked in). Only
   `confidence: "high"` entries are applied automatically; lower-confidence
   entries print a hint and the tool sweeps anyway unless
   `--trust-known-profiles` is passed. The seeded MT6855 entry is `"low"` on
   purpose — see its `caveats`.
3. Otherwise the sweep runs and writes
   `benchmarks/results/raw/<timestamp>-autotune/autotune.json`.

`--export-android-policy` is deliberately stricter than cache reuse: it accepts only a fresh
`verdict=autotuned` sweep whose six gates pass, canonical identity hash matches, winner is an
unpinned prefill/decode pair, and measured stock widths are present. Any rejected input overwrites
the generated Kotlin policy with a disabled value, so a stale winner cannot remain active.

Candidate generation, per detected topology:

| Family | What it is | 2+6 example | 1+3+4 example |
|---|---|---|---|
| `stock` | no `-t`, no `-C`; the baseline for every ratio | 1 case | 1 case |
| `pinned` | one per cluster-prefix set, plus little-only, as `-C <bare hex>` | `c0`, `3f` | `80`, `f0`, `f` |
| `unpinned` | `-t` at each cluster boundary plus wide-gap midpoints | t 2/4/6/8 | t 1/2/4/6/8 |

Measurement hygiene (this is why a sweep takes tens of minutes, not seconds):

- **Warm-up is discarded.** A cold page cache measured 1.11 tok/s against 10.50
  warm; that invocation is recorded with `"scored": false` and never scored.
- **Counterbalanced order.** Candidates run in `rounds`, forward on even rounds
  and reversed on odd, so drift is shared instead of charged to whoever ran last.
- **Cooldown *and* a thermal gate.** After idling `--cooldown` seconds (default
  120, as in `run_suite.py`) the tool polls `dumpsys battery` and only starts the
  next candidate once temperature is back within `tempSlackC` of the sweep's own
  starting temperature.
- **A drifted sweep names no winner.** If temperature rose past `maxTempRiseC`, a
  cooldown gate timed out, or speed correlated with position in the round, the
  verdict is `inconclusive`, the recommendation falls back to stock defaults, and
  nothing is cached. Back-to-back candidates with no cooldown produced a 2.5x
  apparent difference on the test device (pp512 76.30 → 30.70) that was entirely
  thermal — larger than any effect the tool looks for.
- **Thermal status is recorded, not gated.** `Thermal Status: 2` (MODERATE) is
  the *resting state* of the test device, reported at every telemetry point of
  runs that were thermally quiet by temperature (the 27 Jul ngram A/B rose 0.2 C
  while sitting at status 2), partly because the handset is USB-charging
  throughout. Gating on it would return `inconclusive` on every sweep forever
  and never cache a profile. `maxThermalStatus` / `thermalStatuses` stay in the
  report and a warning is printed; pass `--max-thermal-status 1` to opt into a
  strict gate on devices where the status means something.

Scoring (`--stability-k`, `--tg-weight`, `--max-rel-stddev`, `--min-improvement`):
`mean − k·stddev` pooled over all rounds, expressed as a ratio to the stock row,
combined as `tgWeight·tg + (1−tgWeight)·pp`. Any candidate whose relative stddev
exceeds `maxRelStddev` is disqualified outright regardless of its mean, and the
winner must beat stock by `minImprovement` or stock is kept. Full reasoning is in
the `score_rows` docstring in `tools/autotune.py`.

Device-free unit tests: `python -m unittest discover -s tools -p 'test_*.py'`.

## Phase 3 speculative-decoding A/Bs

`phase3-mtp.json` and `phase3-ngram.json` are consumed by
`tools/run_spec_ab.py`, not `run_suite.py`. The compatibility entrypoints
`run_mtp_ab.py` and `run_ngram_ab.py` select the corresponding suite by
default. The runner uses `llama-server` because the pinned `llama-completion`
target does not initialize the common speculative-decoding path. It starts the
server on the device, forwards the port through ADB, and streams fixed greedy
chat completions for the same target model.

The native-MTP modes are:

- baseline: `--spec-type none`
- native MTP: `--model-draft <MTP.gguf> --spec-type draft-mtp
  --spec-draft-n-max 4`

Before building llama.cpp, idempotently check/apply the repository candidate
patch that makes the explicit draft path load correctly:

```sh
python tools/apply_llama_patch.py
python tools/apply_llama_patch.py --apply
```

The check exits successfully both when the patch can be applied and when it is
already applied. The runner records the pinned llama.cpp commit, dirty status,
worktree-diff SHA-256, on-device binary `--version`, and binary SHA-256 so the
candidate build remains identifiable.

Validate or preview without a device:

```sh
python tools/run_mtp_ab.py --validate-only
python tools/run_mtp_ab.py --dry-run --cooldown 0
```

Run the measured five-repetition A/B:

```sh
python tools/run_mtp_ab.py benchmarks/suites/phase3-mtp.json
```

One schemaVersion-1 MTP evidence file is written to
`benchmarks/results/raw/<timestamp>-phase3-mtp/mtp-ab.json`. It contains:

- exact target/draft manifest entries and command lines;
- suite-file SHA-256 and a canonical SHA-256 for every fixed prompt;
- streamed output, output hashes, TTFT, end-to-end latency, server timings,
  drafted/accepted token counts, and acceptance rate;
- battery, thermal, `MemAvailable`, `SwapFree`, process `VmRSS`/`VmHWM`, and
  ZRAM telemetry through load and generation;
- exact-output pairs, decode speedup, feasibility gates, and a
  `pass`/`fail`/`inconclusive` verdict.

The default gate requires both modes to load, all runs and output pairs to
complete, exact greedy outputs to match, actual draft tokens to be reported,
zero ZRAM growth, and at least 1.03x mean decode throughput. No model download
or device file transfer is performed by the runner. The default also requires
no decrease in `SwapFree` across the MTP session. Preflight computes SHA-256 for
both on-device GGUFs and rejects any mismatch with `models/manifest.json`; this
can take several minutes for the 2.19 GB target. It also verifies that the
deployed binary advertises the three required MTP flags before model load.

The corrected one-repetition feasibility run is tracked at
`benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json`.
It failed the promotion gate at 0.98036x decode speed and 37.36% acceptance
while preserving all three non-empty outputs exactly. See
`docs/optimization-notes.md` for the full verdict.

### Zero-weight ngram-mod fallback

`phase3-ngram.json` uses the already-deployed Llama 3.2 1B Q4_0 target in both
modes and adds no model weights:

- baseline: `--spec-type none`
- fallback: `--spec-type ngram-mod --spec-ngram-mod-n-match 24
  --spec-ngram-mod-n-min 4 --spec-ngram-mod-n-max 32`

Validate and preview without contacting the device:

```sh
python tools/run_ngram_ab.py --validate-only
python tools/run_ngram_ab.py --dry-run --repetitions 1 --cooldown 0
```

Run the central five-repetition fallback A/B:

```sh
python tools/run_ngram_ab.py \
  --out-dir benchmarks/results/phase3-feasibility
```

The result is
`benchmarks/results/phase3-feasibility/<timestamp>-phase3-ngram/ngram-ab.json`.
The runner keeps `baseline` and `ngram` as distinct mode labels, requires all
outputs to be non-empty and byte-identical, requires actual drafted tokens and
at least 1.03x mean decode throughput, and checks swap loss, speculative RSS
increase, and battery temperature/rise. ZRAM is still recorded when the kernel
exports its counter; this suite does not turn a missing ZRAM metric into an
automatic inconclusive result.
