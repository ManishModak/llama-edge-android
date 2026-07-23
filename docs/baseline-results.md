# Phase 1 baseline — CPU thread sweep + Vulkan comparison (Llama 3.2 1B Q4_0)

> **This is the OFFICIAL baseline.** Measured 23 Jul 2026 with Linux-built binaries.
> It supersedes the 22 Jul Windows-built run, which is retained at the bottom for
> comparison. One Windows finding did **not** reproduce — see
> [§ Overturned finding](#overturned-finding-t8-does-not-collapse).

**Date:** 2026-07-23 · **Suite:** `benchmarks/suites/phase1-baseline.json` (all 12 cases)
**Device:** Redmi Note 14 5G (`24094RAD4I`, serial `8DYTMRKF755TOBZD`) — MediaTek Dimensity 7025 (MT6855),
2× Cortex-A78 (cpu6/7) + 6× Cortex-A55 (cpu0–5), arm64-v8a, Android 16, 5.6 GB RAM.
GPU: PowerVR B-Series BXM-8-256, Vulkan 1.3.
**Binaries:** `llama-bench` (CPU) and `llama-bench-vulkan` (Vulkan), static, pinned llama.cpp `178a6c4`,
**NDK r28c 28.2.13676358 on Linux (CachyOS, GCC 16.1.1 host)**, `-march=armv8.2-a+dotprod+fp16`.
**Repo commit:** `4462c70`.
**Model:** `llama-3.2-1b-instruct-q4_0`, Q4_0, sha256 `fa0390e7…97a8be8`, 729.75 MiB tensors, mmap on, KV f16.
**Protocol:** 5 repetitions per case, warmup on, 120 s idle cooldown between cases, unpinned
(no `cpuMask` — the scheduler places threads).

**Raw results:** `benchmarks/results/raw/20260723-135809-phase1-baseline/` (12 files).
**Total runtime:** 48 min 17 s wall (08:28:10 → 09:16:26 UTC). **12/12 cases clean**, no failures.
Battery 67 % on USB throughout; thermal status never left `0 (NONE)`; battery temp 34.7 → 38.2 °C.

---

## Results

| case | backend | threads | pp tok/s | tg tok/s | pg tok/s | thermal (batt) |
|---|---|---|---|---|---|---|
| cpu-t2-pp512 | cpu | 2 | 46.87 +/- 10.73 | - | - | 34.7C -> 35.9C |
| cpu-t2-tg128 | cpu | 2 | - | 12.76 +/- 0.12 | - | 35.7C -> 35.8C |
| cpu-t4-pp512 | cpu | 4 | 60.26 +/- 0.94 | - | - | 36.2C -> 36.1C |
| cpu-t4-tg128 | cpu | 4 | - | 10.89 +/- 1.04 | - | 36.6C -> 36.9C |
| cpu-t6-pp512 | cpu | 6 | 70.72 +/- 0.37 | - | - | 36.8C -> 36.7C |
| cpu-t6-tg128 | cpu | 6 | - | 14.16 +/- 0.34 | - | 36.8C -> 37.1C |
| cpu-t8-pp512 | cpu | 8 | **80.04 +/- 1.34** | - | - | 37.1C -> 37.4C |
| cpu-t8-tg128 | cpu | 8 | - | 13.87 +/- 0.90 | - | 37.1C -> 37.5C |
| cpu-t6-pg512-128 | cpu | 6 | - | - | 34.67 +/- 2.44 | 37.3C -> 37.8C |
| vulkan-pp512 | vulkan | 4 | 39.78 +/- 0.03 | - | - | 37.7C -> 37.3C |
| vulkan-tg128 | vulkan | 4 | - | 1.50 +/- 0.00 | - | 37.1C -> 38.2C |
| vulkan-pg512-128 | vulkan | 4 | - | - | 6.37 +/- 0.04 | 37.7C -> 38.2C |

### CPU thread sweep

| threads | pp512 tok/s | tg128 tok/s |
|---:|---:|---:|
| 2 | 46.87 ± 10.73 | 12.76 ± 0.12 |
| 4 | 60.26 ± 0.94 | 10.89 ± 1.04 |
| 6 | 70.72 ± 0.37 | **14.16 ± 0.34** |
| 8 | **80.04 ± 1.34** | 13.87 ± 0.90 |

**Prefill and decode want different thread counts.** Prefill is best at t=8, decode at t=6.

### CPU vs Vulkan

| workload | CPU (best) | Vulkan | CPU advantage |
|---|---:|---:|---:|
| pp512 | 80.04 (t=8) | 39.78 | **2.0×** |
| tg128 | 14.16 (t=6) | 1.50 | **9.4×** |
| pg512+128 | 34.67 (t=6) | 6.37 | **5.4×** |

---

## Observations

1. **Prefill scales monotonically to all 8 cores** (46.9 → 60.3 → 70.7 → 80.0). The six A55s
   do contribute real throughput to compute-bound prefill; this is not a big-core-only workload.

2. **Decode does not** — it peaks at t=6 (14.16) and *regresses* at t=8 (13.87), consistent with
   decode being memory-bandwidth bound rather than compute bound. Extra threads add contention,
   not bandwidth.

3. **The non-monotonic decode anomaly survives re-baselining.** t=4 (10.89) is **worse than t=2**
   (12.76) — a 15 % regression from *adding* two threads.

   > **⚠️ Corrected by Phase 2 (23 Jul).** This observation originally attributed the anomaly to a
   > big.LITTLE *straggler* effect — "A55 threads stall the A78s at ggml's per-op barrier" — and
   > called it the strongest evidence for workstream C. **Profiling refuted that.** There is no
   > stable big/LITTLE split to straggle against: every worker migrates continuously across both
   > clusters (~59 % A78 residency at t=2, ~28 % at t=6), and the per-thread CPU-time spread is only
   > 1.18–1.28× — far too small to cost 15 %. The actual mechanism is **DRAM saturation plus a
   > spin-wait barrier** (`ggml-cpu.c:599`): decode is already at ~11.1 GB/s ≈ 65–75 % of LPDDR4X
   > peak, so every added thread is a core spinning on `yield`, contending for the same memory
   > controller. Six A55s alone match the best full-SoC decode (14.36 vs 14.50), proving the A78s
   > add nothing. See [bottleneck-note.md](bottleneck-note.md).

4. **Vulkan runs correctly but loses on every workload**, catastrophically so on decode (9.4×).
   The device capability readout explains it:
   `int dot: 0` (no `VK_KHR_shader_integer_dot_product`, so Q4_0 matmul gets no integer-dot path),
   `matrix cores: none`, and **16 KB** shared memory (vs 32–64 KB typical), which caps `mul_mm`
   tile sizes. `uma: 1` is the one favourable property. See
   [vulkan-build-notes.md](vulkan-build-notes.md).

5. **Vulkan is remarkably *stable* even though it is slow** — ±0.03 on pp512 and ±0.00 on tg128,
   an order of magnitude tighter than any CPU case. The GPU runs at a flat, low clock and is
   unaffected by the scheduler noise and DVFS ramps that move the CPU numbers. Predictability is
   not the problem; throughput is.

6. **pg is well below naive pp/tg composition.** Composing t=6 pp (70.72) and tg (14.16) for a
   512+128 workload predicts ≈ 42 tok/s; measured is 34.67, i.e. **17 % below** — KV-depth cost is
   real and must be measured, not extrapolated. (Identical 17 % gap to the Windows run, so this is
   a property of the workload, not the toolchain.)

7. **Cold-start DVFS ramp biases whichever case runs first.** `cpu-t2-pp512` ran first on a 34.7 °C
   device and its five reps ramped 29.3 → 44.1 → 51.2 → 54.5 → 55.2 (±10.73, 23 % rel.). A warm
   re-run of the same case gave **46.15 ± 3.48** — the same mean, one third the spread. So the
   *value* is sound but the *variance* is an artifact of case ordering.
   **Methodology fix for future suites:** run a throwaway warmup case first, or discard rep 1.

8. **Memory headroom is tighter than the 5.6 GB figure suggests** — `MemAvailable` sat at
   1.9–2.2 GB during the run with a 730 MiB model resident. The `-c 512` discipline (HANDOFF §7.2)
   remains load-bearing.

---

## Overturned finding: t=8 does *not* collapse

The Windows-built run reported t=8 as unstable and slow — pp512 **58.62 ± 26.74** (46 % rel. stddev)
with reps of 74.8 / 78.9 / 78.1 / **19.6** / 41.7, and tg128 **9.48 ± 2.31**. That drove a HANDOFF
finding that "t=8 collapses unpredictably".

Linux-built binaries show **no such collapse**:

| | Windows-built | Linux-built |
|---|---:|---:|
| t8 pp512 | 58.62 ± 26.74 (46 % rel.) | **80.04 ± 1.34 (1.7 % rel.)** |
| t8 tg128 | 9.48 ± 2.31 (24 % rel.) | **13.87 ± 0.90 (6.5 % rel.)** |

t=8 is now the *best* prefill configuration and is stable. The collapse was an artifact of the
Windows-built binary (or of that run's environment), not a property of the device. This vindicates
HANDOFF's warning to re-baseline before treating any Windows number as an A/B reference, and it
removes "t=8 is unusable" from the Gate G1 evidence set.

**Consequence for Gate G1:** the case for workstream C now rests specifically on the **decode**
anomaly (t=4 < t=2, and t=8 ≤ t=6), not on a general instability at high thread counts.

---

## Impact on Gate G1

PLAN.md's decision table maps "Vulkan ≪ CPU or unstable" → **workstream A (adaptive backend
routing)**. That row assumed a backend worth routing *to*. Here the GPU is slower on prefill
(2.0×), decode (9.4×) *and* combined (5.4×), so a router would never select it — workstream A
would ship a switch that is always off.

The live evidence instead points at **workstream C (big.LITTLE-aware threading)**: prefill wants
8 threads, decode wants 6, and decode actively regresses at t=4 due to A55 stragglers. A
phase-aware thread/affinity policy has a measurable target on this device.

> **⚠️ Superseded by Phase 2 (23 Jul).** Workstream C is **refuted for throughput**. Explicit
> affinity experiments show the decode ceiling (≈14.5 tok/s) is *already reached by the stock
> default*, because decode is DRAM-bound at ~65–75 % of LPDDR4X peak. Pinning buys variance
> (±1.20 → ±0.03) and energy headroom, not speed. The evidence now favours **workstream D**
> (speculative decoding — the only option that reduces bytes-per-token), though PLAN.md's 3B+1B
> sizing does **not** fit the measured 1.84 GB `MemAvailable` and would need a draft-model-free
> variant. Full reasoning and the G1 table: [bottleneck-note.md](bottleneck-note.md).

**Not yet decided** — Gate G1 is 31 Jul.

---

## Superseded: preliminary Windows-built run (22 Jul 2026)

Kept for comparison only. **Do not use as an A/B baseline** — different toolchain.
Raw: `benchmarks/results/raw/20260722-210643-phase1-baseline/` (9 CPU cases; Vulkan was blocked
on Windows). Repo commit `0fcd0ea`, 25 min 29 s wall, 9/9 clean.

| threads | pp512 tok/s | tg128 tok/s |
|---:|---:|---:|
| 2 | 66.11 ± 6.77 | 12.80 ± 0.16 |
| 4 | 63.41 ± 2.65 | 11.19 ± 0.53 |
| 6 | 68.20 ± 0.77 | 14.16 ± 0.20 |
| 8 | 58.62 ± 26.74 | 9.48 ± 2.31 |

pg512+128 @ t=6: 31.98 ± 4.58. Thermal never left NONE; skin 36.5 → 42.2 °C.

**What reproduced:** t=6 decode optimum (14.16 both runs, to 3 s.f.); the t=4 < t=2 decode
anomaly; the ~17 % pg shortfall vs naive composition; thermal status never leaving NONE.
**What did not:** the t=8 collapse (see above); t=2 prefill (66.11 Windows vs 46.5 Linux —
the Linux figure is confirmed by a warm re-run).
