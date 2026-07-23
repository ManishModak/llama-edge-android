# MobileSpec — Execution Plan & Checklist

**Arm Create: AI Optimization Challenge — Mobile AI track**
Plan date: 20 July 2026 (updated 20 Jul, post-scaffold) · Deadline: **14 Aug 2026, 4:00 PM PDT** (= **15 Aug, 4:30 AM IST** — treat **13 Aug evening** as the real deadline)
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

**Guaranteed minimum deliverable** (even if everything goes wrong): reproducible CPU-vs-Vulkan benchmark harness for this device class + thread/core-policy findings on big.LITTLE + a working demo app with A/B benchmark screen. That alone is a legitimate submission.

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
- [ ] Gradle multi-module skeleton (Kotlin + Compose, NDK/CMake wired) — deferred to Phase 4 start; not needed for ADB benchmarking
- [x] `tools/device_snapshot.py` (cross-OS Python) · [ ] push+run bench script · [ ] result collection script
- [x] `docs/` templates: benchmark-methodology, reproducibility, optimization-notes (+ PLAN.md)
- [x] `models/manifest.json` — sha256 still TODO after first model download

**First native build (the real Phase 0 exit test)**
- [x] Cross-compile **CPU-only** binaries for arm64, NDK 28, `-march=armv8.2-a+dotprod+fp16`, static — done 20 Jul (note: upstream renamed `llama-cli` → **`llama-completion`**; new `llama-cli` is a server client needing a host compiler)
- [x] Download bring-up model **Llama 3.2 1B Q4_0** to `D:\models`; sha256 in manifest
- [x] `adb push` + smoke generation ✓ — coherent output on device
- [x] **Exit criterion MET** (20 Jul). First numbers (2×A78 pinned, pp64/tg32): **pp 45.1 tok/s, tg 12.2 tok/s**
- ⚠ Learnings: always pass explicit small `-c` to llama-completion (default 128K ctx thrashes 5.6 GB RAM → 0.26 tok/s); toybox `taskset` wants bare hex mask (`taskset c0` = A78 pair); details in `docs/phase0-report.md`

### Phase 1 — Baseline harness & first numbers (Jul 24–28 · ~11 h)

- [ ] Build **Vulkan** variant (`GGML_VULKAN=ON`) → does it even initialize on BXM-8-256? *(highest-information experiment of the project)* — **blocked on Windows by Smart App Control; do this first thing on Linux**
- [x] `tools/run_suite.py` + `summarize_results.py` (Python, cross-OS): llama-bench matrix over ADB, thermal/battery/memAvailable before+after, one schemaVersion-1 JSON per case
- [x] Benchmark matrix v1 CPU portion (5 reps, warm, 120 s cooldowns) — **9/9 cases clean, 25 min**
  - [x] CPU thread sweep 2/4/6/8 × pp512/tg128 + pg512+128
  - [ ] Vulkan comparison (blocked, see above) · [ ] cold-load + peak RSS per backend · [ ] context 2048/4096
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

**Findings that shape Gate G1:**
- **t=6 is optimal and by far the most stable** (~1 % rel. stddev vs 46 % at t=8)
- **Scaling is non-monotonic — t=4 is *worse* than t=2** (big.LITTLE straggler effect: A55 threads stall A78s at ggml's per-op barrier) → **direct evidence for workstream C**
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
| Decode clearly memory-bandwidth-bound and speculation viable | **D: Adaptive speculative decoding** (needs 3B Q4 target ≈1.9 GB + 1B draft ≈0.7 GB — fits in 5.6 GB, but risky; only pick with strong evidence) |

> **Phase 2 evidence (23 Jul) — read [bottleneck-note.md](bottleneck-note.md) before deciding.**
> The last row now matches: decode is DRAM-bound at 11.1 GB/s ≈ 65–75 % of LPDDR4X peak, and six
> A55s alone match the best full-SoC decode (14.36 vs 14.50 tok/s), so the A78s contribute nothing.
> - **C is refuted for throughput** — the ceiling is already hit by the stock default; pinning buys
>   variance and energy, not speed.
> - **A is weak** — the GPU loses on all three workloads, so a router ships a switch always off.
> - **B is dead** — `int dot: 0`, no matrix cores, 16 KB shared memory.
> - **D's sizing in this row is wrong for this device.** Measured `MemAvailable` is **1.84 GB**, not
>   5.6 GB, so a 3B target + 1B draft (2.6 GB) would spill to ZRAM. D is only viable **draft-model
>   free**: native MTP (available at our pin per §2), prompt-lookup/n-gram, or self-speculation.

### Phase 3 — The optimization (Aug 1–7 · ~15 h)

- [ ] Feature branch on the llama.cpp fork from pinned upstream master; **one idea, small diff**
- [ ] Implement change; keep `baseline` and `optimized` builds differing *only* in this change
- [ ] Correctness: `test-backend-ops` (if operator touched), golden-output comparison (fixed seed/prompt), perplexity spot-check on a small text
- [ ] A/B llama-bench: full matrix, ≥5 reps, mean+stddev, same battery/thermal starting state
- [ ] Sustained-run A/B (does the win survive throttling?)
- [ ] Log every experiment (incl. failures) in `docs/optimization-notes.md`

**⛔ Gate G2 (7 Aug):** improvement repeatable and correct?
- **Yes** → freeze the change; anything further goes in "future work"
- **No** → pivot: the submission story becomes the harness + device findings + adaptive fallback controller ("this device class needs X, here's the data and the tool"). Do **not** start a second optimization after 7 Aug.

**Sequential revisit rule:** the non-chosen workstreams are queued, not discarded. A second workstream may be started **only if** the first is fully done (G2 = Yes: frozen, validated, documented) **and** it's still before 7 Aug. Same rules apply: one at a time, own branch, own A/B run, baseline = the already-frozen optimized build. After the deadline, the queue becomes the upstream/future-work roadmap in the README.

### Phase 4 — Android app (Aug 3–10, interleaved with Phase 3 · ~13 h)

*Interleave rule (solo): when blocked on native builds/benchmarks, switch to app tasks.*

- [ ] `engine-api`: `InferenceEngine`, `ModelConfig`, `GenerationConfig`, `TokenEvent`, `BenchmarkConfig/Result` (Kotlin, per Part II §5.1)
- [ ] `engine-llama`: thin JNI bridge (load / generate→Flow / cancel / unload / capabilities); one serialized native queue; timing captured **natively**
- [ ] App screens (Compose), in priority order — cut from the bottom if needed:
  - [ ] **Benchmark screen**: run suite, live progress, before/after comparison cards, JSON export/share
  - [ ] **Device screen**: SoC, cores, Vulkan device/driver, thermal + memory live
  - [ ] **Chat screen**: prompt → streamed tokens, backend selector (normal vs optimized mode toggle)
  - [ ] Model picker (import GGUF from storage, show sha256)
- [ ] Thermal (`PowerManager.getThermalStatus`) + battery + memory telemetry recorded into every in-app run
- [ ] Release build variant with signing for the demo

### Phase 5 — Evidence, docs, demo, submission (Aug 8–13 · ~12 h)

**Final evidence bundle**
- [ ] Clean-device final benchmark session: baseline vs optimized, full matrix, raw JSON committed/attached to a GitHub Release
- [ ] Headline chart (before/after decode tok/s + TTFT + sustained curve) as PNG for README
- [ ] Claim in the required format: *"On Redmi Note 14 5G (Dimensity 7025, driver 25.1), for Llama 3.2 1B Q4_0 tg128, the change improves X from A to B across N reps, correctness preserved."*

**Repository/docs**
- [ ] README in judge order: 1-sentence pitch → headline chart → demo link → what changed & why → reproduction steps (exact commits, model sha256, commands) → methodology → limitations/fallbacks → upstream plan
- [ ] `CHANGES_FOR_CHALLENGE.md` — exact work done during the submission window
- [ ] `docs/reproducibility.md` complete: JDK/NDK/CMake/AGP versions, build commands, ADB steps
- [ ] Fresh-clone test: **follow your own README on a clean checkout** and fix gaps
- [ ] License headers/file verified (Apache-2.0)

**Demo video (<3 min, script from Part I §9)**
- [ ] 0:00–0:20 problem · 0:20–0:45 device+model · 0:45–1:35 live A/B on same prompt (tokens/s visible) · 1:35–2:10 adaptive decision / acceptance / phase split · 2:10–2:40 sustained+memory · 2:40–3:00 repo + reproduce command
- [ ] Screen-record via `adb` / scrcpy; voiceover; upload YouTube (unlisted is fine)

**Submission**
- [ ] Devpost form complete with video link + repo — **submit 12 Aug**
- [ ] 13 Aug: buffer for Devpost issues only. Do not code.

**Upstream (only if G2 = Yes and time remains)**
- [ ] Rebase branch on upstream master; self-authored PR text; `docs/upstream-contribution.md` with llama-bench evidence
- [ ] Open an upstream *issue/discussion* describing the finding even if the PR isn't ready — costs 30 min, adds Impact evidence

---

## 3. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Vulkan broken/slow on BXM driver | **High** (documented) | Test in first Phase 1 session; failure = evidence for workstream A; CPU path is always the fallback |
| 60 h budget slips | High | Gates G1/G2 are hard cut lines; app screens cut bottom-up; LiteRT-LM and upstream PR pre-declared as stretch |
| Thermal noise hides gains | Medium | Fixed starting temp, airplane mode, screen-min, cooling gaps between reps, report variance; sustained runs decide |
| 1B model too fast for speculation | High | Speculation only selectable at G1 with evidence; 3B target variant sized to fit RAM |
| OOM (5.6 GB real RAM) | Medium | Q4 only, ctx ≤ 4K, monitor peak RSS in every run |
| Solo illness / life event | Medium | Guaranteed-minimum deliverable defined in §1; submit whatever is green on 12 Aug |
| llama.cpp AI-contribution policy | Certain | You author/understand/disclose; Claude assists with analysis, harness, app; PR text self-written |

## 4. Definition of done (submission-blocking)

- [ ] Clean checkout builds with documented toolchain
- [ ] One command/app action runs the standard suite
- [ ] Every result traceable: device, app commit, llama.cpp commit, model sha256, backend, config
- [ ] Baseline vs optimized differ only in the intended change
- [ ] ≥1 metric improves reproducibly **or** adaptive controller demonstrably avoids a slower path
- [ ] Correctness checks pass; unsupported-device fallback works
- [ ] App demos chat + A/B benchmark without log-diving
- [ ] Repo flipped **private → public**, Apache-2.0 visible, README judge-ordered, video <3 min

## 5. Weekly cadence (2–3 h/day)

| Week | Dates | Focus | Hours |
|---|---|---|---|
| 1 | Jul 20–26 | Phase 0 + Phase 1 (Vulkan answer by Jul 25) | ~15 |
| 2 | Jul 27–Aug 2 | Phase 2, Gate G1, start Phase 3 | ~15 |
| 3 | Aug 3–9 | Phase 3 finish, Gate G2, Phase 4 app | ~18 |
| 4 | Aug 10–13 | Phase 5: evidence, README, video, **submit Aug 12** | ~12 |
