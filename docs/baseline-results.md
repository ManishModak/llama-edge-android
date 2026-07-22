# Phase 1 baseline — CPU thread sweep (Llama 3.2 1B Q4_0)

**Date:** 2026-07-22 · **Suite:** `benchmarks/suites/phase1-baseline.json` (CPU cases only)
**Device:** Redmi Note 14 5G (`24094RAD4I`, serial `8DYTMRKF755TOBZD`) — MediaTek Dimensity 7025 (MT6855),
2× Cortex-A78 (cpu6/7 @ 2.5 GHz) + 6× Cortex-A55 (cpu0–5 @ 2.0 GHz), arm64-v8a, Android 16, 5.6 GB RAM.
**Binary:** `llama-bench`, CPU-only static build from pinned llama.cpp `178a6c4`, NDK 28.2.13676358,
`-march=armv8.2-a+dotprod+fp16`. **Repo commit:** `0fcd0ea`.
**Model:** `llama-3.2-1b-instruct-q4_0`, Q4_0, sha256 `fa0390e7…97a8be8`, 729.75 MiB tensors, mmap on, KV f16.
**Protocol:** 5 repetitions per case, warmup on, 120 s idle cooldown between cases, unpinned
(no `cpuMask` — the scheduler places threads).

**Raw results (git-ignored):** `benchmarks/results/raw/20260722-210643-phase1-baseline/`
(9 files, ~40 KB; each carries the full `llama-bench` payload under `rawBench`, including per-repetition
`samples_ts`, plus thermal/battery/memory snapshots either side of the case).

**Total runtime:** 25 min 29 s wall (15:36:44 → 16:02:13 UTC), of which 16 min was cooldown and
~9.5 min actual benchmarking. 9/9 cases completed, no failures, no JSON parse errors.

---

## Results

| case | backend | threads | pp tok/s | tg tok/s | pg tok/s | thermal (batt) |
|---|---|---|---|---|---|---|
| cpu-t2-pp512 | cpu | 2 | 66.11 +/- 6.77 | - | - | 33.3C -> 34.0C |
| cpu-t2-tg128 | cpu | 2 | - | 12.80 +/- 0.16 | - | 34.3C -> 34.9C |
| cpu-t4-pp512 | cpu | 4 | 63.41 +/- 2.65 | - | - | 35.3C -> 35.5C |
| cpu-t4-tg128 | cpu | 4 | - | 11.19 +/- 0.53 | - | 35.6C -> 36.5C |
| cpu-t6-pg512-128 | cpu | 6 | - | - | 31.98 +/- 4.58 | 37.8C -> 38.7C |
| cpu-t6-pp512 | cpu | 6 | 68.20 +/- 0.77 | - | - | 36.2C -> 36.7C |
| cpu-t6-tg128 | cpu | 6 | - | 14.16 +/- 0.20 | - | 36.4C -> 36.9C |
| cpu-t8-pp512 | cpu | 8 | 58.62 +/- 26.74 | - | - | 36.7C -> 37.8C |
| cpu-t8-tg128 | cpu | 8 | - | 9.48 +/- 2.31 | - | 37.5C -> 37.9C |

Rearranged as a sweep (mean ± stddev over 5 reps):

| threads | pp512 tok/s | tg128 tok/s |
|---:|---:|---:|
| 2 | 66.11 ± 6.77 | 12.80 ± 0.16 |
| 4 | 63.41 ± 2.65 | 11.19 ± 0.53 |
| **6** | **68.20 ± 0.77** | **14.16 ± 0.20** |
| 8 | 58.62 ± 26.74 | 9.48 ± 2.31 |

Combined prefill+decode, `-pg 512,128` at t=6: **31.98 ± 4.58 tok/s** over the 640-token workload.

Per-repetition samples (tok/s), from `rawBench[].samples_ts`:

| case | rep 1 | rep 2 | rep 3 | rep 4 | rep 5 |
|---|---:|---:|---:|---:|---:|
| t2 pp512 | 75.05 | 70.28 | 66.14 | 59.67 | 59.43 |
| t4 pp512 | 67.50 | 61.67 | 64.30 | 62.86 | 60.72 |
| t6 pp512 | 67.31 | 67.92 | 67.98 | 68.40 | 69.39 |
| t8 pp512 | 74.79 | 78.86 | 78.12 | 19.59 | 41.73 |
| t2 tg128 | 12.62 | 12.98 | 12.76 | 12.67 | 12.94 |
| t4 tg128 | 10.75 | 10.83 | 11.68 | 11.85 | 10.82 |
| t6 tg128 | 14.17 | 13.81 | 14.20 | 14.25 | 14.34 |
| t8 tg128 | 12.27 | 8.80 | 6.00 | 10.21 | 10.12 |
| t6 pg512+128 | 34.89 | 23.87 | 33.08 | 34.09 | 33.97 |

---

## Device state

| | start (15:36:44 UTC) | end (16:02:13 UTC) | drift |
|---|---|---|---|
| Thermal status | 0 (NONE) | 0 (NONE) | none — never left NONE |
| Battery temp | 33.3 °C | 38.7 °C | **+5.4 °C** |
| Skin temp (HAL) | 36.5 °C | 42.2 °C | +5.7 °C |
| Battery level | 99 % | 99 % | 0 (USB-attached) |
| MemAvailable | 1.67 GB | 1.86 GB | flat, 1.39–2.07 GB across all 18 samples |

Prep performed: `adb shell svc power stayon usb`; `adb shell am kill-all` (background apps);
confirmed idle foreground (Settings), <15 % aggregate CPU before the run.

**Caveats against the `docs/reproducibility.md` checklist:**
- Airplane mode / Wi-Fi could not be toggled reliably over adb — radios were left as-is.
- Screen brightness was not changed (avoided touching system settings).
- Battery was at **99 % and USB-attached (`status: 2`, charging, 500 mA cap)**, not the prescribed
  60–80 % on battery. The device is charging whenever adb is the transport, so the battery-delta
  column is meaningless for this run and thermal readings are mildly pessimistic (charger heat).
  Thermal status still never left NONE, so no cooldown escalation was needed.
- Runs were **unpinned** — the suite carries no `cpuMask`, so `-t N` is a thread-count sweep, not a
  core-affinity sweep. See observation 2.

---

## Observations

**1. t=6 is the optimal thread count for both prefill and decode — and also the most stable.**
It wins outright on prefill (68.20 tok/s) and decode (14.16 tok/s), and it has by far the tightest
spread (±0.77 and ±0.20, i.e. ~1 % relative stddev, versus 10 % at t=2 and 46 % at t=8). The
Phase 0 default of `-t 2` leaves **11 % of decode throughput** on the table. Recommendation for
Phase 2+: default to 6 threads on this SoC, and treat t=6 as the CPU reference line the Vulkan and
optimization work must beat.

**2. Scaling is non-monotonic — 4 threads is *worse* than 2. This is a big.LITTLE straggler effect.**
pp512 goes 66.11 → 63.41 → 68.20 → 58.62 and tg128 goes 12.80 → 11.19 → 14.16 → 9.48 as threads
go 2 → 4 → 6 → 8. Adding the first two A55 threads (t=4) *loses* ~4 % prefill and ~13 % decode
versus t=2. ggml splits work evenly per thread and joins on a barrier each op, so A55 threads
(~2.0 GHz, in-order) become stragglers the two A78s wait on; two extra slow workers cost more in
barrier stall than they contribute. By t=6 there are enough A55 lanes for the aggregate to finally
overtake t=2, but only by 3 % on prefill. **Note this is thread count, not affinity** — with no
`cpuMask`, "t=2" is not the same as "A78-only". As a cross-check, Phase 0's `taskset c0`-pinned
(genuinely A78-only) run measured 12.16 tok/s tg32 versus 12.80 tok/s for unpinned t=2 here — close
enough that unpinned t=2 is landing mostly on the big cores. A true `cpuMask` A78-only-vs-all-core
comparison is a Phase 2 item; the mere 3 % headroom that six threads buy over two suggests explicit
big-core pinning (`-C c0`) may well beat the all-core configuration once barrier costs are removed.

**3. t=8 oversubscribes the device and collapses, unpredictably.**
Eight benchmark threads on eight cores leaves nothing for `system_server` and the adb shell, and the
result is the worst mean *and* catastrophic variance: pp512 reps ran 74.79, 78.86, 78.12, then
**19.59**, then 41.73 — a 4× swing within one case. Decode is the same story (12.27 down to 6.00).
Note that the first three t=8 prefill reps are the fastest single measurements in the whole run
(78.9 tok/s, +14 % over t=6's best) — the hardware *can* do it, but only until the scheduler preempts
a worker and the whole barrier group stalls. Do not ship `-t 8` on this device; the tail latency is
indefensible even though the peak is attractive.

**4. Prefill is ~4.8× decode, and decode is the bottleneck for user-visible latency.**
At t=6, prefill runs at 68.20 tok/s and decode at 14.16 tok/s. Decode is memory-bandwidth-bound on a
Q4_0 1B model (every token streams the full 730 MiB of weights), which is why it responds so weakly to
extra compute — t=2 → t=6 gains only 10.6 % on decode and 3.2 % on prefill. Practical consequence:
a 512-token prompt costs ~7.5 s of prefill, and each subsequent token ~71 ms. Phase 2 optimization
effort is worth far more on the decode path than on prefill.

**5. The combined pg512+128 case runs 17 % below the naive composition of the separate numbers.**
Predicted from the isolated t=6 measurements: 512/68.20 + 128/14.16 = 7.51 + 9.04 = 16.55 s for 640
tokens = 38.7 tok/s. Measured: **31.98 tok/s** (20.0 s). The gap is real work the isolated tests do
not do — `tg128` decodes from an empty KV cache, whereas the `pg` case decodes at depth 512–640, so
attention reads a growing KV cache and each token costs more. Any TTFT/throughput model built by
composing separate pp/tg numbers will be optimistic; measure at depth (`-d`/`n-depth`) for realistic
figures. This case also carries the run's single worst outlier (rep 2 at 23.87 vs ~34 for the rest).

**6. Boost-clock decay is visible *within* a single 5-rep case, and no memory pressure was observed.**
The very first case of the run (t=2 pp512, cold device) decays monotonically 75.05 → 59.43 tok/s
across its five reps — a 21 % fall in ~50 s while the battery moved only 33.3 → 34.0 °C. That is DVFS
boost budget expiring, not thermal throttling. It explains the outsized ±6.77 stddev on an otherwise
well-behaved configuration, and argues that the first case in any suite should be treated as warm-up.
Separately, the Phase 0 memory-thrashing gotcha did **not** recur: `MemAvailable` stayed between 1.39
and 2.07 GB across all 18 snapshots and no case showed anomalously low tok/s from swapping.
`llama-bench` at this commit derives context from prompt+gen (max 640 tokens here), so the KV cache
stays negligible and the 128K default never comes into play — confirming that gotcha is specific to
`llama-completion`'s explicit `-c`.

---

## Pending

- **Vulkan comparison is pending.** The three `vulkan-*` cases in `phase1-baseline.json`
  (`vulkan-pp512`, `vulkan-tg128`, `vulkan-pg512-128`) were skipped for this run via the new
  `--only-backend cpu` flag, because no `llama-bench-vulkan` binary exists on the device yet — the
  Vulkan build is currently blocked on the host. Status and details: `docs/vulkan-build-notes.md`.
  Re-run the full suite (drop `--only-backend`) once that binary is built and pushed; the CPU numbers
  above are the reference line.
- **Core-affinity sweep** (`cpuMask` / `-C`) — A78-only vs A55-only vs all-core at matched thread
  counts. Phase 2, workstream C. See observation 2.
- **Depth sweep** (`-d`) to characterise decode cost as the KV cache grows. See observation 5.

## Reproduce

```bash
adb shell svc power stayon usb
python tools/run_suite.py benchmarks/suites/phase1-baseline.json \
  --serial 8DYTMRKF755TOBZD --only-backend cpu --ndk-version 28.2.13676358
python tools/summarize_results.py benchmarks/results/raw/<timestamp>-phase1-baseline \
  -o docs/baseline-results.md
```
