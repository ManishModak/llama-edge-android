# MobileSpec — Execution Plan & Checklist

**Arm Create: AI Optimization Challenge — Mobile AI track**
Plan date: 20 July 2026 (strategy updated 5 Aug for the phase-aware autotuner pivot) · Deadline: **14 Aug 2026, 4:00 PM PDT** (= **15 Aug, 4:30 AM IST** — treat **13 Aug evening** as the real deadline)
Constraints: **solo**, **2–3 h/day → ~55–65 total hours**, balanced ambition (competition first, upstream PR if it falls out naturally).
Repo: **https://github.com/ManishModak/llama-edge-android** (private until submission — flip public by 12 Aug). Canonical copy of this plan: `docs/PLAN.md` in the repo.

---

## 0. Verified facts (measured 20 Jul 2026 — do not re-derive)

### Device (Redmi Note 14 5G, `24094RAD4I`, ADB serial `8DYTMRKF755TOBZD`)
| Item | Verified value |
|---|---|
| SoC | **MediaTek Dimensity 7025 (MT6855)** |
| CPU | 8 cores: **2× Cortex-A78** (0xd41) + **6× Cortex-A55** (0xd05) |
| SIMD features | `asimddp` (dotprod ✓), `fphp/asimdhp` (fp16 ✓), **no i8mm, no SVE** |
| GPU | **PowerVR B-Series BXM-8-256**, driver build 25.1@6715691 |
| Vulkan | **1.3** (feature level 1, compute ✓) |
| RAM | 5.6 GB physical usable (8 GB marketing incl. swap-based extension) |
| OS | Android 16, arm64-v8a |

### Dev machine (Windows 11)
- Android Studio + JBR Java ✓ · NDK **27.0** and **28.2** ✓ · SDK CMake **3.22.1** ✓ · platform-tools/adb 37 ✓ · platforms up to android-36 ✓
- Standalone ninja/cmake **not on PATH** (use SDK CMake; install ninja if NDK builds need it)
- Python 3.12 ✓, Git ✓

### Challenge (confirmed on Devpost 20 Jul 2026)
- Prizes: $3,000 overall / $2,000 runner-up / $1,000 best-in-track. Judging: **Tech 40 / WOW 25 / Impact 20 / DX 15**.
- Requirements: public GitHub repo, **MIT or Apache-2.0 visible**, docs + build instructions, demo video **optional but <3 min** (YouTube/Vimeo).

### Key research finding
PowerVR BXM Vulkan on Android has **documented driver problems** (llama.cpp community + IMG forums report slow or broken Vulkan on non-Adreno mobile GPUs, including BXM display/compute issues). This is **the project's opportunity and its biggest risk**: expect Vulkan to possibly lose to CPU or misbehave. That outcome is *evidence for the adaptive-routing story*, not failure — but Phase 1 must test it in the first week.

---

## 1. Strategy for a 60-hour solo budget

### Submission goal after Gate G2

> **Build and prove a phase-aware CPU policy autotuner for llama.cpp GGUF inference on Arm
> Android.** MobileSpec discovers heterogeneous CPU topology, measures stock defaults, searches
> separate prefill and decode thread policies, and promotes a policy only when counterbalanced,
> thermally gated measurements show a reproducible improvement without a correctness regression.

The novelty is the reusable, evidence-gated policy selection—not a new model and not a remembered
thread constant. The performance goal is still mandatory: the final submission needs a measured
stock-versus-autotuned improvement in end-to-end latency, decode throughput, stability, or a
clearly scoped combination of those metrics.

**Submission support boundary:**

- Arm64 Android devices reachable through ADB.
- CPU inference in the pinned llama.cpp runtime.
- GGUF models supported by that runtime and registered in `models/manifest.json`.
- Topology-derived thread count and affinity candidates, with separate prefill
  (`n_threads_batch`) and decode (`n_threads`) policies as the final target.
- Physical performance validation on the Redmi Note 14 5G. Other topologies are supported by
  generic discovery logic and tests, but are not claimed as physically validated.

Do **not** describe the submission as tuning “any model on any device.” Linux SBC/cloud adapters,
GPU/backend tuning, Arm kernel selection, and cross-device validation belong to Future Work.

The two source PDFs assume more hours than we have. Cuts made deliberately:

1. **llama-bench over ADB first, app second.** All Phase 1–3 evidence comes from cross-compiled `llama-bench`/`llama-cli` pushed to `/data/local/tmp` — no app needed to start measuring. The app is built only after the optimization direction is locked.
2. **One optimization, not two.** Pick exactly one workstream at Gate G1. The adaptive controller is only built if Phase 3 evidence justifies it *or* Phase 3 fails (then the controller becomes the headline).
3. **LiteRT-LM baseline is a stretch goal**, not a requirement. One comparable number is nice for the README; skip if behind schedule.
4. **Upstream PR = prepared branch + evidence, not a merged PR.** llama.cpp restricts AI-generated PRs; you must author, understand, and disclose. A clean branch + llama-bench data documented in `docs/upstream-contribution.md` scores the Impact points even unmerged.
5. **Submission target is 12 Aug**, leaving 13 Aug as pure buffer.

**Cross-OS setup (user dual-boots Windows/Linux):**
- One clone per OS on its native filesystem: `C:\Projects\llama-edge-android` (NTFS) on Windows, home dir (ext4) on Linux. GitHub is the sync mechanism — push before switching OS.
- The exFAT drive holds **data only**: GGUF models, raw benchmark bundles, demo footage. Both clones point at it via `LLAMA_EDGE_MODELS` env var.
- All `tools/` scripts are Python (no PowerShell/bash-only tooling); `.gitattributes` normalizes line endings.

6. **Ship the method, not the constant** (added 27 Jul, post-G2). Every result must be framed so it
   applies beyond this handset. Device-specific numbers are fine as *evidence*; they are not the
   deliverable. A hardcoded `t=6` is a case study — a runtime policy that derives the right thread
   count from core topology is an optimization. The challenge asks for Arm phones/tablets/laptops,
   so anything that only works on MT6855 is scored as a fraction of what it could be.

**Guaranteed minimum deliverable** (even if the phase split does not beat stock): reproducible
CPU/Vulkan evidence, a topology-derived autotuner that safely retains stock defaults when no
candidate passes its gates, and a working demo app with an A/B benchmark screen.

**Mapping to the challenge's stated criteria** (added 27 Jul — judge on these, not on our internal phases):
| Criterion | What we have | Gap |
|---|---|---|
| Arm-specific optimization | Heterogeneous-core discovery plus measured prefill/decode policy selection | Implement and prove the phase-pair policy, not a constant |
| Developer experience | Gated, provenance-fingerprinted harness that refuses to promote a failed run | One command/app action and a reusable cached profile |
| Model speed | Absolute tok/s and TTFT across a thread sweep | Complete the immutable stock-vs-phase-aware A/B |
| Model size | Nothing | Not pursuing |
| Model quality | Exact-output verification only | Not pursuing |

---

## 2. Phase plan with checklists

### Phase 0 — Registration, repos, first build (Jul 20–23 · ~7 h)

**Admin**
- [ ] Register for the challenge on Devpost (Mobile AI track)
- [x] Create repo `llama-edge-android` with **Apache-2.0 license in root** — done 20 Jul as **private**; ⚠ must be public before submission (12 Aug)
- [x] Add `third_party/llama.cpp` submodule — pinned to **upstream `178a6c4`** (b10069, 19 Jul, shallow clone; `git fetch --unshallow` if history needed)
- ~~Fork llama.cpp now~~ → **deferred to Phase 3**: submodule pins upstream directly until we patch it. If repo is still private then, push the patched llama.cpp to a standalone private repo (forks can't be private) and swap the submodule URL; proper fork + PR only when going public.

**Local scaffold** (Claude does this)
- [x] Directory structure (lean set: `app`, `engine-api`, `engine-llama`, `tools/`, `docs/`, `benchmarks/{suites,prompts,results}`) — commit `2e22c9f`, pushed
- [x] Gradle multi-module skeleton (Kotlin + Compose, NDK/CMake wired) — implemented in Phase 4
- [x] `tools/device_snapshot.py` (cross-OS Python) · [x] push+run benchmark harness · [x] result collection/reporting
- [x] `docs/` templates: benchmark-methodology, reproducibility, optimization-notes (+ PLAN.md)
- [x] `models/manifest.json` with verified model identities and SHA-256 values

**First native build (the real Phase 0 exit test)**
- [x] Cross-compile **CPU-only** binaries for arm64, NDK 28, `-march=armv8.2-a+dotprod+fp16`, static — done 20 Jul (note: upstream renamed `llama-cli` → **`llama-completion`**; new `llama-cli` is a server client needing a host compiler)
- [x] Download bring-up model **Llama 3.2 1B Q4_0** to `D:\models`; sha256 in manifest
- [x] `adb push` + smoke generation ✓ — coherent output on device
- [x] **Exit criterion MET** (20 Jul). First numbers (2×A78 pinned, pp64/tg32): **pp 45.1 tok/s, tg 12.2 tok/s**
- ⚠ Learnings: always pass explicit small `-c` to llama-completion (default 128K ctx thrashes 5.6 GB RAM → 0.26 tok/s); toybox `taskset` wants bare hex mask (`taskset c0` = A78 pair); details in `docs/phase0-report.md`

### Phase 1 — Baseline harness & first numbers (Jul 24–28 · ~11 h)

- [x] Build and run the **Vulkan** variant (`GGML_VULKAN=ON`) — completed on Linux; it loses to CPU on every official workload
- [x] `tools/run_suite.py` + `summarize_results.py` (Python, cross-OS): llama-bench matrix over ADB, thermal/battery/memAvailable before+after, one schemaVersion-1 JSON per case
- [x] Benchmark matrix v1 CPU portion (5 reps, warm, 120 s cooldowns) — **9/9 cases clean, 25 min**
  - [x] CPU thread sweep 2/4/6/8 × pp512/tg128 + pg512+128
  - [x] Vulkan comparison · [ ] cold-load + peak RSS per backend · [ ] context 2048/4096
- [ ] 10–15 min **sustained run** per backend; log tokens/s over time + thermal transitions
- [x] Result JSON schema (Part II §9.2) implemented; raw results committed · [ ] deterministic prompts for `llama-completion` correctness runs
- [x] **`docs/baseline-results.md`** written — table + 6 observations
- [ ] *Stretch:* LiteRT-LM same-class model, one CPU + one GPU number

**Preliminary CPU baseline** (Llama 3.2 1B Q4_0, Windows-built binaries — ⚠ re-run on Linux-built binaries before treating as the official A/B baseline):

| threads | pp512 tok/s | tg128 tok/s |
|---:|---:|---:|
| 2 | 66.11 ± 6.77 | 12.80 ± 0.16 |
| 4 | 63.41 ± 2.65 | 11.19 ± 0.53 |
| **6** | **68.20 ± 0.77** | **14.16 ± 0.20** |
| 8 | 58.62 ± 26.74 | 9.48 ± 2.31 |

pg512+128 @ t=6: 31.98 ± 4.58 tok/s. Thermal never left NONE; skin +5.7 °C over the run.

**Preliminary Windows-build findings that shaped Gate G1 (historical; later Linux/Phase 2 evidence
supersedes their mechanism and stability interpretation):**
- **t=6 is optimal and by far the most stable** (~1 % rel. stddev vs 46 % at t=8)
- **Scaling was non-monotonic — t=4 was worse than t=2.** The initial A55-straggler explanation
  was later refuted; retain the samples, not that universal mechanism claim.
- **t=8 collapses unpredictably** (reps: 74.8, 78.9, 78.1, **19.6**, 41.7) — highest peak, indefensible tail
- **Decode is memory-bandwidth bound** (prefill 4.8× decode; decode barely responds to threads)
- **pg runs 17 % below naive pp/tg composition** — KV-depth cost is real; benchmark the combined case, not the parts
- **Boost-clock (DVFS) decay is visible within a single 50 s case** with only +0.7 °C — not thermal. Sustained runs must account for this
- No memory thrashing under llama-bench (MemAvailable 1.39–2.07 GB); the Phase 0 128K-context gotcha is `llama-completion`-specific

### Phase 2 — Profiling & bottleneck selection (Jul 29–31 · ~7 h)

- [x] ~~`simpleperf` sample of CPU decode~~ — **impossible on this device**: the MT6855 kernel refuses `perf_event_open` for every event type even at `perf_event_paranoid=-1`. Substituted `/proc/<pid>/task/*/stat` sampling (`tools/profile_decode.sh` + `tools/analyze_placement.py`), which gave the A78-vs-A55 residency answer anyway
- [x] Perfetto trace of one benchmark run — `tools/perfetto/decode-trace.pbtxt`; trace at `benchmarks/results/profiles/20260723-105825/decode-t4.pftrace`
- [x] Vulkan path — `GGML_VK_PERF_LOGGER` is a *runtime* env var, no rebuild needed. Superseded as a priority: the affinity experiments settled the bottleneck question without it
- [x] `test-backend-ops` on Vulkan/BXM — **3 reportable findings**: bf16 pipeline-creation abort (kills the process, blocks the sweep from reaching MUL_MAT), `iq2_s` dequant corruption, Q4_0 `MUL_MAT` error up to ERR 191.8 at `n=512`
- [x] **`docs/bottleneck-note.md`** written — decode is DRAM-bound at 65–75 % of LPDDR4X peak; **workstream C refuted**, D favoured

**⛔ Gate G1 (31 Jul): choose exactly ONE workstream.** Decision table:

| Observed evidence | Chosen workstream |
|---|---|
| Vulkan ≪ CPU or unstable (likely per research) | **A: Adaptive backend routing** (prefill/decode phase split, thermal-aware, cached device profile) — Vulkan findings become documented evidence |
| Vulkan competitive but sync/dispatch-bound | **B: Vulkan dispatch/data-movement tuning** for BXM (fewer barriers, batch tuning, buffer reuse) |
| CPU-bound in quantized matvec, 8-thread config loses to 2×A78 | **C: big.LITTLE-aware threading/core-pinning policy** (+ possibly Q4_0 dotprod repack tuning) |
| Decode clearly memory-bandwidth-bound and speculation viable | **D: Adaptive speculative decoding** (prefer a native MTP head or zero-weight n-gram drafting; a second full draft model is outside this device's memory budget) |

> **Phase 2 evidence (23 Jul) — read [bottleneck-note.md](bottleneck-note.md) before deciding.**
> The last row now matches: decode is DRAM-bound at 11.1 GB/s ≈ 65–75 % of LPDDR4X peak, and six
> A55s alone match the best full-SoC decode (14.36 vs 14.50 tok/s), so the A78s contribute nothing.
> - **C is refuted for throughput** — the ceiling is already hit by the stock default; pinning buys
>   variance and energy, not speed.
> - **A is weak** — the GPU loses on all three workloads, so a router ships a switch always off.
> - **B is dead** — `int dot: 0`, no matrix cores, 16 KB shared memory.
> - **D's original 3B+1B sizing is wrong for this device.** Measured `MemAvailable` is **1.84 GB**
>   with the 0.73 GB Llama baseline resident, so a second full draft model leaves no safe room for
>   target weights, KV cache, and runtime allocations. Use a compact native MTP head or a zero-weight
>   method instead.
>
> **G1 LOCKED — 26 Jul 2026: choose D, adaptive speculative decoding.** Phase 3 starts with native
> MTP on Gemma 4 E2B QAT using the smallest acceptable target quantization. The feasibility gate is
> target + MTP head load at `-c 512`, no ZRAM growth, coherent output, and a repeatable same-model
> MTP-off versus MTP-on throughput win. If native MTP fails that gate before implementation work,
> the in-workstream fallback is zero-weight n-gram/prompt-lookup speculation on Llama 3.2 1B.
>
> **Native-MTP feasibility result — 26 Jul 2026: rejected on this device.** The smallest compatible
> target and 59 MB head both load, draft acceptance is 37.36 %, and all three greedy outputs match,
> but the candidate is **0.980x** baseline (4.823 vs 4.920 tok/s) and consumes swap. The measured
> gate therefore failed before a five-repetition promotion run. At that point Phase 3 continued to
> the declared zero-weight n-gram fallback on the resident 0.73 GB Llama target; the following
> result records why that fallback was also closed.
>
> **N-gram fallback result — 27 Jul 2026: rejected, and the raw number is an artifact.** Artifact:
> `benchmarks/results/phase3-feasibility/20260727-204314-phase3-ngram/ngram-ab.json`, verdict
> `fail`. Two resource gates failed: peak RSS rose **139,780 KB** over baseline (limit 65,536) and
> `SwapFree` dropped **91,392 KB** (limit 65,536). The recorded **2.97x** decode speedup must not be
> reported, because the suite measured a cache hit, not drafting:
> - acceptance was **1076 / 1076 = 100.0 %**, which n-gram drafting does not achieve on real text;
> - repetition 1 was ~9.7–9.9 tok/s on **all three** prompts — level with baseline — while every run
>   from repetition 2 on was ~31–35 tok/s;
> - the runner launched **one** `llama-server` for all 15 runs, so the n-gram cache persisted across
>   requests while greedy decoding at a fixed seed re-sent each prompt five times. Reps 2–5 replayed
>   text already resident in the cache.
>
> Even `repetitive-sequence`, the prompt built to favour n-gram, gained nothing on first exposure
> (9.94 tok/s). That is the honest signal and it is consistent with the Phase 2 DRAM ceiling.
> **Suite defect to fix before any re-run:** restart the server per repetition (or vary the prompt
> per repetition) so each measurement starts with a cold cache.

**⛔ Gate G2 result — 27 Jul 2026: No.** Workstream D is closed. Both speculative candidates were
measured and refuted on this device, each with a committed evidence file. Per the G2 "No" branch,
the submission pivots to the harness-and-policy story — see Phase 3B.

### Phase 3B — Phase-aware portable core-policy autotuner (the submission optimization)

**Why this and not more speculation.** Native MTP and zero-weight n-gram speculation both failed
their evidence gates. The remaining measured opportunity is phase-dependent CPU policy: prefill is
compute-heavy and favours a wider configuration, while decode is bandwidth-sensitive and measured
faster and more consistently with a narrower configuration. The winning values may change with the
device, model, build, context, and workload; runtime measurement is the general contribution.

Do not claim that A55 threads universally stall A78 threads on every Arm big.LITTLE SoC. On this
device the supported explanation is DRAM saturation plus synchronization/spin-wait overhead. The
portable claim is that phase optima can differ and must be measured rather than assumed.

**Implemented core tuner:**

- [x] Detect topology from `/proc/cpuinfo`, cpufreq, present CPUs, and memory; unknown MIDRs degrade
      safely instead of selecting a guessed policy.
- [x] Derive thread-count and affinity candidates from cluster boundaries rather than device names.
- [x] Run short, counterbalanced, thermally gated `llama-bench` rounds with a discarded warm-up.
- [x] Score prefill and decode relative to measured stock defaults, penalise variance, and refuse to
      select an unstable or thermally contaminated winner.
- [x] Cache by device/model and consult a confidence-gated known-profile table.
- [x] Preserve provenance and individual samples in a machine-readable report.
- [x] Unit-test topology parsing, candidate generation, scoring, cache behaviour, and failure gates
      across representative 2+6, 4+4, and 1+3+4 topologies.

**Required phase-aware completion:**

- [x] Replace the single-policy candidate with a phase pair: prefill threads
      (`n_threads_batch`) plus decode threads (`n_threads`), with affinity used only where the
      runtime can apply it correctly and reproducibly. Implemented 6 Aug: the pinned
      `llama-bench` patch adds independent `-tb`/`-t` threadpools and reports both widths; the
      topology-derived phase-pair candidates deliberately omit affinity because a common mask
      cannot express distinct phase placement.
- [x] Extend profile identity to include llama.cpp build/commit, model SHA-256, context, benchmark
      shape, and scoring/workload class; stale profiles must trigger a re-sweep, not silent reuse.
      The cache key now includes a canonical profile-identity hash covering the exact on-device
      binary SHA-256, pinned source commit, model hash, pp/tg/context shape, rounds/repetitions,
      workload class, and scoring policy. Patch `0002` now supplies and reports an explicit
      512-token context so the tuner and APK no longer fingerprint different allocations.
- [x] Complete the interrupted three-round device run and retain its raw artifact. Completed
      7 Aug at `benchmarks/results/raw/20260807-232905-autotune/autotune.json` (SHA-256
      `7afd9ae000d57bf1bce0e3d38de13d99ffd779a34bbb591f9e41dabffc56a7ab`); all individual
      stock/candidate samples, commands, gate outcomes, and provenance are retained.
- [x] Run a frozen stock-default versus phase-pair confirmation with the same binary/model, at least
      five samples per phase, counterbalanced order, temperature gates, and correctness checks.
      The app confirmation contains five runs/mode plus a discarded warm-up; every paired output
      hash matched.
- [x] Measure end-to-end generation and a sustained session so a synthetic pp/tg win is not mistaken
      for a user-visible win. Three consecutive no-cooldown suites ran for 14.37 minutes and retained
      15 native-timed samples/mode, TTFT, end-to-end duration, thermal/memory snapshots, and hashes.
- [x] Integrate the selected phase pair into the Android engine and make unsupported/stale profiles
      fail closed to stock defaults. Runtime support is complete: a verified policy carries the
      measured stock and optimized decode/prefill widths, JNI switches both widths per mode, and
      stale/unsupported policy identity disables optimized mode. A one-command strict exporter
      validates every promotion gate, canonical identity, device/model/build/context, measured
      stock widths, and runtime-supported phase-pair shape before generating the bundled policy.
      The promoted phone profile is `pp8-tg2`; wrong device/model/build/context identities disable it.
- [x] Verify model import, generation, cancellation/reuse, benchmark UI, and JSON export on the phone.
      SAF copy/hash/load, baseline and optimized generation, A/B, export, cancellation, and a
      successful 128-token post-cancel reuse run were exercised on serial `8DYTMRKF755TOBZD`.

**What generalizes and what does not:**

| Claim | Scope |
|---|---|
| MobileSpec derives and measures candidates instead of shipping a device constant | Implemented method for Arm Android + llama.cpp GGUF |
| Prefill and decode can prefer different CPU policies | Measured on this device; expected elsewhere but must be re-measured |
| A particular phase pair beats stock defaults | Device/model/build/workload-specific; claim only after the final bundle |
| Decode reaches 65–75% of estimated LPDDR4X peak | Redmi/MT6855-specific; the roofline method is reusable |

**✅ Final optimization gate — YES (8 Aug):** the three-round synthetic sweep passed all six
promotion gates and the frozen app confirmation preserved exact paired outputs. Across the
14.37-minute no-cooldown session, optimized decode averaged 11.178 ± 0.191 tok/s versus
5.390 ± 0.868 stock over 15 samples/mode. Freeze `pp8-tg2` only for the exact report identity;
all other device/model/build/context/workload combinations still fail closed to stock.

### Phase 4 — Android app (Aug 3–10, interleaved with Phase 3 · ~13 h)

*Interleave rule (solo): when blocked on native builds/benchmarks, switch to app tasks.*

- [x] `engine-api`: `InferenceEngine`, `ModelConfig`, `GenerationConfig`, `TokenEvent`, `BenchmarkConfig/Result` (Kotlin, per Part II §5.1)
- [x] `engine-llama`: thin JNI bridge (load / generate→Flow / cancel / unload / capabilities); one serialized native queue; timing captured **natively**
- [x] App screens (Compose), in priority order:
  - [x] **Benchmark screen**: run suite, live progress, before/after comparison cards, JSON export/share
  - [x] **Device screen**: SoC, cores, Vulkan device/driver, thermal + memory live
  - [x] **Chat screen**: prompt → streamed tokens, backend selector (optimized controls fail closed until supported)
  - [x] Model picker (import GGUF from storage, show sha256)
- [x] Thermal (`PowerManager.getThermalStatus`) + battery + memory telemetry recorded into every in-app run
- [x] Release build variant with signing for the demo

### Phase 5 — Evidence, docs, demo, submission (Aug 8–13 · ~12 h)

**Final evidence bundle**
- [x] Complete the three-round topology-derived sweep, including measured stock defaults.
- [ ] Clean-device final benchmark session: stock-default vs phase-aware policy, full matrix, raw JSON committed/attached to a GitHub Release
- [x] Headline chart (stock-default vs autotuned decode tok/s + TTFT + **variance/p99**) as PNG for README
- [x] Two claims, scoped differently and both stated:
  - *General:* "MobileSpec derives and measures phase-specific llama.cpp CPU policies from Arm Android topology, and keeps stock defaults unless a candidate passes performance, variance, and thermal gates."
  - *Device-specific:* "On Redmi Note 14 5G (Dimensity 7025), with the frozen Llama 3.2 1B Q4_0 build and workload, the selected prefill/decode pair changed X from A to B across N samples, correctness preserved."
- [x] State the single-device limitation plainly, and invite others to run the harness and add a row

**Repository/docs**
- [x] README in judge order: 1-sentence pitch → headline chart → demo link → what changed & why → reproduction steps (exact commits, model sha256, commands) → methodology → limitations/fallbacks → upstream plan
- [x] `CHANGES_FOR_CHALLENGE.md` — exact work done during the submission window
- [x] `docs/reproducibility.md` complete: JDK/NDK/CMake/AGP versions, build commands, ADB steps
- [ ] Fresh-clone test: authenticated shallow clone + Android release pass; public clone,
      native/model/device-suite sequence still pending
- [x] License headers/file verified (Apache-2.0)

**Demo video (<3 min, script from Part I §9)**
- [ ] 0:00–0:20 problem · 0:20–0:45 device+model · 0:45–1:35 live stock vs phase-aware A/B · 1:35–2:10 topology discovery and separate prefill/decode selection · 2:10–2:40 sustained result + variance · 2:40–3:00 repo + one-command reproduction
- [ ] Screen-record via `adb` / scrcpy; voiceover; upload YouTube (unlisted is fine)

**Submission**
- [ ] Devpost form complete with video link + repo — **submit 12 Aug**
- [ ] 13 Aug: buffer for Devpost issues only. Do not code.

**Upstream (only if G2 = Yes and time remains)**
- [ ] Rebase branch on upstream master; self-authored PR text; `docs/upstream-contribution.md` with llama-bench evidence
- [ ] Open an upstream *issue/discussion* describing the finding even if the PR isn't ready — costs 30 min, adds Impact evidence

### Future work — explicitly outside the submission critical path

1. **Cross-device validation and profile federation.** Reproduce on additional Dimensity,
   Snapdragon, Tensor, Exynos, and modern tri-cluster phones; promote bundled profiles only after
   independent devices agree. Add signed/community-contributed profile bundles.
2. **Linux Arm adapters.** Replace the ADB transport with a local executor for Raspberry Pi,
   Rockchip boards, Ampere, and AWS Graviton. Add NUMA-aware candidates where the platform exposes
   multiple memory domains.
3. **More models and workloads.** Validate dense and MoE GGUF families, multiple quantizations,
   context lengths, prompt sizes, batch sizes, and chat versus long-document workloads. A profile
   must remain keyed to exact model/build/workload identity.
4. **Additional runtime knobs.** Explore `n_batch`/`n_ubatch`, KV-cache type, flash attention,
   mmap/mlock policy, and context sizing only after the thread-policy search is stable. Use a
   bounded/Pareto search so first-run tuning remains practical.
5. **Backend routing.** Generalise the controller to CPU, Vulkan, OpenCL, or accelerator backends
   when correctness gates pass. The current PowerVR Vulkan failures remain a documented test case,
   not a backend to ship.
6. **Arm capability-aware kernels.** Detect and evaluate dotprod, i8mm, SVE/SVE2, SME, and FP16/BF16
   paths on hardware that actually exposes them. Kernel work requires roofline evidence; do not
   rewrite arithmetic on a path already limited by DRAM bandwidth.
7. **Energy and sustained objectives.** Add joules/token, battery drain, thermal throttling, and
   Pareto selection between speed, energy, and tail latency.
8. **Speculation revisit.** Re-test MTP, n-gram, prompt lookup, or future draft strategies only on
   device/model pairs with adequate memory and a cold-cache, same-target verification protocol.
9. **Upstream/runtime integration.** Expose phase-pair profiles through a stable llama.cpp/API
   integration and move tuning into the app so users can select “Optimize this model” without ADB.

---

## 3. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Vulkan broken/slow on BXM driver | **High** (documented) | Test in first Phase 1 session; failure = evidence for workstream A; CPU path is always the fallback |
| 60 h budget slips | High | Gates G1/G2 are hard cut lines; app screens cut bottom-up; LiteRT-LM and upstream PR pre-declared as stretch |
| Thermal noise hides gains | Medium | Fixed starting temp, airplane mode, screen-min, cooling gaps between reps, report variance; sustained runs decide |
| Phase-pair gain disappears end to end | High | Require real generation and sustained confirmation after synthetic pp/tg selection |
| Profile becomes stale after a build/model/workload change | High | Include build, model, context, and workload identity; re-sweep on mismatch |
| **Result over-fitted to one handset** | **High** (materialised 27 Jul) | Ship runtime auto-detect instead of constants; label every claim general vs device-specific; §1 rule 6 |
| **Benchmark confound inflates a claim** | **High** (materialised 27 Jul, n-gram 2.97x) | Inspect acceptance rate, warmup split, and cache/server lifetime before reporting any number; cold-start each repetition |
| Only one device available for validation | Certain | State it as a limitation; make the harness trivially runnable so others can add rows |
| Solo illness / life event | Medium | Guaranteed-minimum deliverable defined in §1; submit whatever is green on 12 Aug |
| llama.cpp AI-contribution policy | Certain | You author/understand/disclose; Claude assists with analysis, harness, app; PR text self-written |

## 4. Definition of done (submission-blocking)

- [ ] Clean checkout builds with documented toolchain
- [x] One command/app action runs the standard suite
- [x] Every result traceable: device, app commit, llama.cpp commit, model sha256, backend, config
- [x] Baseline vs optimized differ only in the intended change, and **baseline = stock defaults**
- [x] ≥1 user-relevant metric improves reproducibly **or** the controller demonstrably rejects an unsafe/slower candidate and retains stock without a speedup claim
- [x] No claim rests on a measurement whose confound has not been checked — cache state, warmup, and
      acceptance rate are inspected before any number is reported (see the 27 Jul n-gram artifact)
- [x] Every claim is labelled *general* or *this-device-only*; no device-specific number is presented
      as a general result
- [x] Correctness checks pass; unsupported-device fallback works
- [x] App demos chat + A/B benchmark without log-diving
- [ ] Repo flipped **private → public**, Apache-2.0 visible, README judge-ordered, video <3 min

## 5. Weekly cadence (2–3 h/day)

| Week | Dates | Focus | Hours |
|---|---|---|---|
| 1 | Jul 20–26 | Phase 0 + Phase 1 (Vulkan answer by Jul 25) | ~15 |
| 2 | Jul 27–Aug 2 | Phase 2, Gate G1, start Phase 3 | ~15 |
| 3 | Aug 3–9 | Phase 3 finish, Gate G2, Phase 4 app | ~18 |
| 4 | Aug 10–13 | Phase 5: evidence, README, video, **submit Aug 12** | ~12 |
