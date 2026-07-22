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
| `contextSize` | Recorded as metadata (llama-bench has no ctx flag) | result `model.contextSize` |
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
- **Context size**: no `-c`/context flag on `llama-bench` at this commit; context is
  derived from prompt+gen. `contextSize` is carried as result metadata only. Use
  `-d`/`n-depth` (not yet in this suite) to bench at a prefilled depth.
- **Vulkan binary name**: the suite assumes a Vulkan build pushed as
  `llama-bench-vulkan`. If your build is named differently or lives elsewhere,
  override `binary`/`binDir` on those cases. Until that binary exists, run the
  CPU half only with `python tools/run_suite.py <suite.json> --only-backend cpu`
  (the flag is repeatable and filters on each case's `backend` field).
- **big.LITTLE pinning**: the Dimensity 7025 is 2×A78 + 6×A55. The `t2` case only
  *intends* A78-only; real pinning needs `cpuMask` once the core index layout is
  confirmed on-device.
