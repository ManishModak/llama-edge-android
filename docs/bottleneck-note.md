# Bottleneck note — where the time actually goes (Phase 2)

**Date:** 2026-07-23 · **Device:** Redmi Note 14 5G, MediaTek Dimensity 7025 (MT6855),
2× Cortex-A78 (cpu6–7) + 6× Cortex-A55 (cpu0–5), LPDDR4X, 5.6 GB RAM, Android 16.
**Model:** Llama-3.2-1B-Instruct Q4_0, 729.75 MiB of tensors.
**Raw:** `benchmarks/results/profiles/20260723-105825/`, `docs/baseline-results.md`.

---

## TL;DR

**Decode is memory-bandwidth-bound and already at the hardware ceiling.**
Best decode (14.5 tok/s) streams 729.75 MiB of weights per token = **11.1 GB/s**, which is
**65–75 % of the LPDDR4X theoretical peak** — i.e. the practical streaming ceiling.

The consequence is blunt: **no threading policy can make decode faster on this device.**
The only way up is to *move fewer bytes per token*.

Prefill is a different regime — genuinely compute-bound, scales to all 8 cores.

---

## The measurement that settles it

Decode (tg128), 3 reps, 30 s cooldowns, explicit affinity via `-C`/`--cpu-strict`:

| config | decode tok/s |
|---|---:|
| t=6 unpinned (default best) | **14.50 ± 0.07** |
| t=6 **A55-only** (`-C 0x3F`, no big cores at all) | **14.36 ± 0.34** |
| t=2 A78-only (`-C 0xC0`) | 12.76 ± 0.03 |
| t=2 unpinned | 12.15 ± 1.20 |
| t=8 unpinned | 13.80 ± 0.90 |
| t=8 all-strict (`-C 0xFF`) | 11.21 ± 2.47 |

**Six Cortex-A55 cores alone match the best full-SoC configuration** (14.36 vs 14.50, within
noise). The two A78s — roughly 2.5–3× the per-core throughput — contribute **nothing** to decode.
That only happens when the bottleneck is off-core. It is DRAM.

Prefill (pp512) behaves the opposite way, confirming the two phases are in different regimes:

| config | prefill tok/s |
|---|---:|
| t=8 unpinned | **81.64 ± 0.79** |
| t=6 unpinned | 71.57 ± 0.28 |
| t=6 A55-only | 70.58 ± 0.48 |
| t=2 A78-only | 54.88 ± 0.29 |

Here both clusters contribute real throughput and more cores keep helping.

---

## Why extra threads make decode *worse*

Thread→core placement sampled at 20 Hz from `/proc/<pid>/task/*/stat`
(`tools/profile_decode.sh` → `tools/analyze_placement.py`):

| threads | tok/s | cores busy | **core-seconds per token** |
|---:|---:|---:|---:|
| 2 | 11.79 | 2.27 | 0.192 |
| 4 | 11.18 | 4.04 | 0.361 |
| 6 | 12.40 | 5.93 | 0.478 |
| 8 | 8.75 | 7.25 | **0.828** |

Cores-busy tracks thread count almost exactly — **every worker stays ~100 % busy no matter how
many there are.** CPU cost per token rises **4.3×** from t=2 to t=8 while throughput *falls*.

The cause is in the source. `ggml_barrier` is a pure spin-wait — no futex, no sleep, no backoff:

```c
// ggml/src/ggml-cpu/ggml-cpu.c:598-601
// wait for other threads
while (atomic_load_explicit(&tp->n_barrier_passed, memory_order_relaxed) == n_passed) {
    ggml_thread_cpu_relax();          // ggml-cpu.c:520 -> ARM "yield" hint only
}
```

(We build with `-DGGML_OPENMP=OFF`, so this `#else` branch is the one that compiles.)

A thread that reaches the barrier early does not yield the core — it burns it. With decode
bandwidth-saturated, every added thread is a core spinning on `yield`, competing for the same
memory controller and the same thermal/power budget. `--poll 0` does not help
(t=6: 13.32 vs 14.50), because the cost is the spin *between* barriers, not the poll interval.

Threads are also **never stably placed**: no worker holds a big core. At t=2 each thread averages
~59 % A78 residency; by t=6 it is ~28 %. The scheduler migrates them continuously across both
clusters.

### Correction to the Phase 1 hypothesis

`docs/baseline-results.md` observation 3 attributed the non-monotonic decode curve to
"A55 threads stalling the A78s at the barrier" — a big.LITTLE **straggler** effect. **That is
wrong.** There is no stable big/LITTLE split to straggle against (all threads migrate across both
clusters), and the measured per-thread CPU-time spread is only 1.18–1.28×, far too small to explain
a 15 % throughput loss. The real mechanism is bandwidth saturation plus a spin barrier that
converts surplus threads into pure contention. The observation has been corrected in place.

---

## Vulkan / PowerVR BXM

Vulkan loses on every workload (prefill 2.0×, decode 9.4×, combined 5.4× — see baseline). Phase 2
adds three driver findings from `test-backend-ops`:

1. **Hard abort on bf16.** Creating `mul_mat_vec_bf16_f32_f32` returns
   `vk::Device::createComputePipeline: ErrorUnknown`; llama.cpp does not catch it, so the process
   dies. The device advertises `bf16: 0`, yet the pipeline is requested anyway. This **blocks
   `test-backend-ops` from ever reaching MUL_MAT** on this device — the full sweep aborts there,
   and `GGML_VK_DISABLE_BFLOAT16=1` does not prevent it. Working around it needs
   `-p "type_a=q4_0"`-style filtering.
2. **`iq2_s` dequant is silently wrong.** 4 × `GET_ROWS(type=iq2_s)` at ERR ≈ 2.0 — garbage, not
   rounding.
3. **Q4_0 `MUL_MAT` has real errors**, including one catastrophic case:
   `MUL_MAT(q4_0, m=576, n=512, k=576)` → **ERR = 191.8** vs a 5e-4 tolerance, plus four cases at
   0.003–0.038. `n=512` is a prefill-batch shape.

Of 347 total failures in the (partial) sweep, **343 are marginal precision** — ERR 1–5e-7 against a
1e-7 tolerance, uniform across SWIGLU/GEGLU/EXP/SET_ROWS. That is a BXM fp32-strictness
characteristic, not a bug.

**Caveat that matters:** despite (3), Vulkan generates *correct, coherent* output for this model
(verified by fixed-seed golden comparison against CPU — both answer "Rome"). The failing shapes are
not ones Llama-3.2-1B hits. So the Phase 1 Vulkan numbers are valid **for this model**, but the
backend is not trustworthy for arbitrary models on this GPU.

---

## Profiling constraint worth recording

**simpleperf does not work on this device.** The MT6855 kernel refuses `perf_event_open` for
*every* event type — hardware and software alike — even with `perf_event_paranoid = -1`:

```
simpleperf E event_selection_set.cpp:262] Event type 'cpu-cycles' is not supported on the device
```

`simpleperf list` advertises the PMU events; opening any of them fails. So symbol-level sampling
was unavailable and Phase 2 relied on `/proc` thread-state sampling plus Perfetto ftrace
(`tools/perfetto/decode-trace.pbtxt`; note Perfetto only reads configs from
`/data/misc/perfetto-configs/`). Anyone reproducing this work on a MediaTek device should expect
the same wall.

---

## What this means for Gate G1

| workstream | verdict on this evidence |
|---|---|
| **A — adaptive backend routing** | Weak. The GPU loses on all three workloads, so a router ships a switch that is always off. |
| **B — Vulkan dispatch tuning** | Dead. 9.4× behind on decode with `int dot: 0`, no matrix cores, 16 KB shared memory. Not closable by tuning. |
| **C — big.LITTLE threading** | **Refuted for throughput.** The ceiling (≈14.5 tok/s) is already reached by the stock default. Pinning buys variance (±1.20 → ±0.03) and energy, not speed. |
| **D — speculative decoding** | **Best fit on the physics** — it is the one technique that attacks bytes-per-token — but see the memory blocker below. |

**Memory blocker for D as specified.** PLAN.md sizes workstream D as a 3B Q4 target (~1.9 GB) plus
a 1B draft (~0.7 GB) = 2.6 GB, on the basis that 5.6 GB of RAM is enough. Measured `MemAvailable`
during benchmarking is **1.84 GB** (`SwapFree` 3.8 GB, i.e. ZRAM). 2.6 GB of weights would spill to
compressed swap — fatal for a workload already pinned at the DRAM ceiling. **D as written does not
fit.** A draft-model-free variant needs no second set of weights and does fit:

- **Native MTP (multi-token prediction).** HANDOFF §2 records that Gemma 4 native MTP landed
  upstream in b9549 and is therefore **available at our pinned `178a6c4`**. An MTP model predicts
  several tokens per forward pass with no draft model and no second weight set — exactly the
  bytes-per-token reduction this device needs. Requires switching model family; needs a memory and
  quality check first.
- **Prompt-lookup / n-gram speculation.** Zero extra weights, model-agnostic, works with the
  existing Llama-3.2-1B. Weaker acceptance rate, but the cheapest thing to try.
- **Self-speculation via layer skipping.** No extra weights; more invasive.

**The unused resource is the interesting part.** Decode leaves the two A78s idle — A55-only matches
full-SoC throughput. Speculation is precisely the technique that converts spare *compute* into
fewer *memory passes*: verify k draft tokens in one weight-streaming pass. The idle A78s are the
budget to pay for drafting.

**Secondary, cheap, and real:** decode on A55-only delivers 99 % of peak throughput while leaving
both big cores free. For a mobile submission, **energy per token** is a legitimate headline metric
and this is a genuine win — it is just not a *throughput* win.

**Recommendation:** D, in its draft-model-free form, with the A55-only energy result as a supporting
finding. Not yet committed — G1 is 31 Jul.
