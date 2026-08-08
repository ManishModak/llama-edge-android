# Changes for the Arm Create challenge

This file records work completed in this repository during the challenge window. It distinguishes
committed, measured work from Phase 3–5 work that is still in progress.

## Evidence boundary

- Challenge work began with root commit `2e22c9f` on 20 July 2026.
- The official Phase 1/2 project evidence ends at
  `54aabbde5b9c31340f685ba6075a00222b8908f8`. Phase 3 adds a worktree-fingerprinted device
  artifact rather than pretending that later source was already committed.
- The official Phase 1 run itself records project commit
  `4462c70587f9cdd6d00b67b5964cac060014c7a3`, llama.cpp commit `178a6c4`, and
  model SHA-256 `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`.
- The measured Phase 3 artifact is
  `benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json`, SHA-256
  `81e92389437bfe9b02ab7c0b9d5dd41a29f5308bfbbd4711692b4ff114e58932`.

## Work completed

### Project and reproducibility scaffold

- Created the Apache-2.0 repository structure for the Android app, engine modules, benchmark
  suites, tools, models, documentation, and pinned third-party code.
- Pinned llama.cpp at `178a6c44937154dc4c4eff0d166f4a044c4fceba`.
- Added a model manifest that keeps weights out of Git and records source, quantization, license,
  size estimate, and SHA-256 when verified.
- Added cross-OS planning and Windows-to-Linux handoff documentation.

### Arm Android bring-up

- Cross-compiled static arm64-v8a CPU binaries with Android NDK r28c and
  `-march=armv8.2-a+dotprod+fp16`, matching the MT6855's measured features.
- Deployed `llama-completion` and `llama-bench` to an unrooted Redmi Note 14 5G.
- Verified coherent Llama 3.2 1B Q4_0 generation.
- Diagnosed the model's 128K default context as a mobile-memory failure mode and standardized
  explicit small contexts for end-to-end runs.

### Data-driven benchmark harness

- Added `tools/device_snapshot.py` for device identity/state capture.
- Added `tools/run_suite.py` for ADB-driven benchmark matrices with per-case schema-version-1 JSON,
  complete llama-bench payloads, provenance, thermal/battery/memory readings, dry-run support,
  backend filtering, explicit serial selection, and controlled cooldowns.
- Added `tools/summarize_results.py` for Markdown summaries.
- Added the committed `phase1-baseline` suite: CPU 2/4/6/8-thread prefill/decode, CPU combined
  generation, and three Vulkan comparison cases.

### Vulkan cross-build and device characterization

- Identified the Windows Smart App Control failure affecting freshly built host shader tools.
- Moved the reproducible build to Linux.
- Pinned Vulkan-Headers `v1.4.350` at
  `8864cdc896bbc2a9b6eb36b3218fc9ef57908d77`.
- Added a CMake package shim for the NDK's SPIR-V headers and documented the required include-path
  injection.
- Built and ran llama.cpp's Vulkan backend on PowerVR BXM-8-256.
- Recorded the relevant hardware limitations: no integer-dot extension, no matrix cores, and
  16 KiB shared memory.

### Official Phase 1 evidence

- Committed all twelve raw result JSONs from the Linux-built suite.
- Measured best CPU `pp512` at `80.04 ± 1.34 tok/s` with eight threads.
- Measured best CPU `tg128` at `14.16 ± 0.34 tok/s` with six threads.
- Measured CPU advantages over Vulkan of 2.0× for prefill, 9.4× for decode, and 5.4× for combined
  generation.
- Preserved the cold-start DVFS variance and documented why new suites need a predeclared warmup or
  first-repetition policy.
- Corrected the earlier Windows-built interpretation after it failed to reproduce on the official
  Linux toolchain.

### Phase 2 diagnosis

- Added `tools/profile_decode.sh` and `tools/analyze_placement.py`.
- Sampled worker placement and CPU time from `/proc/<pid>/task/*/stat` at 20 Hz.
- Added a reusable Perfetto configuration and committed the trace.
- Documented that `simpleperf` cannot open hardware or software events on this MT6855 kernel.
- Ran explicit affinity experiments showing six Cortex-A55 cores match full-SoC decode while two
  Cortex-A78 cores do not raise the ceiling.
- Identified DRAM saturation plus ggml's spin-wait barrier as the supported decode mechanism,
  replacing the earlier big.LITTLE-straggler hypothesis.
- Added fixed-seed CPU/Vulkan output evidence and filtered Vulkan operator evidence.
- Phase 2 evidence favored adaptive speculative decoding as the only workstream that directly
  attacks target-weight bytes streamed per emitted token.

### Gate G1 decision after the measured evidence snapshot

- On 26 July 2026, G1 was locked on adaptive speculative decoding.
- Native MTP with the smallest Gemma 4 E2B QAT target and matching compact head was the first
  feasibility candidate.
- Native MTP subsequently failed its measured gate. Zero-weight n-gram also failed: its apparent
  gain was a persistent-cache replay artifact, first-exposure throughput did not improve, and
  resource gates failed. Workstream D is closed.
- There is still no Phase 3 speedup claim.

### Phase 3 infrastructure after the measured evidence snapshot

- Replaced the provisional Gemma entries with the selected UD-Q2_K_XL target and matching MTP head,
  including exact file names, byte sizes, download URLs, SHA-256 values, compatibility, and
  licensing fields.
- Added a one-line pinned llama.cpp patch that makes the speculative loader open the configured
  draft path instead of loading the target path a second time.
- Added an idempotent patch checker/applicator.
- Cross-built `llama-server` for Android arm64/API 28 from the patched pinned source. The unstripped
  host artifact hashes to
  `41677f22d4bf9ec94270cb22b287f2e4356379c0cff7252e72d62739119b5743`.
- Added a same-target MTP-off/on suite with repetitive, code, and ordinary-chat prompts.
- Added an ADB/HTTP streaming runner that verifies on-device binary/model fingerprints and records
  paired output, timings, MTP acceptance, RSS, `MemAvailable`, `SwapFree`, ZRAM, battery, thermal,
  source state, and an explicit gate verdict.
- Validated the suite, unit tests, and dry-run command.

### Phase 3 native-MTP feasibility result

- Deployed the exact server, target, and MTP head; device preflight passed all size, SHA-256,
  binary, and required-flag checks.
- Both modes loaded and all three requests per mode completed.
- All three greedy MTP outputs matched their baseline pair exactly.
- MTP proposed 605 draft tokens and accepted 226 (37.355%).
- Mean decode was 4.91956 tok/s baseline versus 4.82293 tok/s MTP: **0.98036×**.
- `SwapFree` dropped 450,620 kB in baseline and 150,028 kB in MTP; ZRAM telemetry was unavailable.
- The artifact verdict is **fail** because no-swap and minimum-1.03× speed gates failed.

This was one repetition per prompt, beginning at thermal status 2. It is sufficient to reject the
candidate before a promotion run, not to claim repeatable performance.

### Phase 3B phase-aware CPU policy

- Added a topology-derived, counterbalanced, thermally gated policy autotuner that measures stock
  defaults and refuses unstable, contaminated, or sub-threshold recommendations.
- Patched pinned `llama-bench` to execute and report separate decode `-t` and prefill `-tb`
  threadpools; packaged the change as an idempotently applicable second patch.
- Added topology-wide-prefill plus topology-derived-decode phase-pair candidates. Phase-pair
  candidates omit affinity because one shared mask cannot safely express distinct phase placement.
- Extended cache identity with the exact on-device binary SHA-256, llama.cpp source commit, model
  SHA-256, context/pp/tg shape, rounds/repetitions, workload class, and scoring policy. Missing or
  stale identity fails closed to a new sweep; generic known profiles are hints only.
- Added explicit `-c/--ctx-size` support and resolved `n_ctx` reporting to the same pinned
  benchmark patch, closing a 96-token synthetic versus 512-token APK context mismatch.
- Cross-compiled the patched Android `llama-bench`; all 80 Python harness tests pass, including a
  full fake-device regression that selects a real `-t 6 -tb 8` pair.
- Added fail-closed Android phase-policy plumbing. A verified policy carries the exact measured
  stock and optimized decode/prefill widths; JNI switches both per generation mode, while stale
  model/build/context identity disables optimized mode.
- Added a discarded benchmark warm-up, counterbalanced A/B order, per-run output hashes, exact
  correctness comparison, and policy/baseline traceability in exported JSON.
- Added a strict report-to-APK policy exporter. It accepts only a fresh fully gated unpinned phase
  pair, verifies the canonical identity hash, and emits a disabled policy on any failed/stale input.

The physical three-round sweep completed on 7 Aug. The immutable report
`benchmarks/results/raw/20260807-232905-autotune/autotune.json` selected `pp8-tg2` against stock
`pp8-tg8` and passed all six gates. Its SHA-256 is
`7afd9ae000d57bf1bce0e3d38de13d99ffd779a34bbb591f9e41dabffc56a7ab`.

The Android confirmation and three no-cooldown sustained suites passed exact output-hash
correctness. Across 15 sustained runs/mode, decode improved from `5.3897 +/- 0.8677 tok/s` to
`11.1777 +/- 0.1908 tok/s` (`2.0739x`) and mean end-to-end time fell from 25,244.9 ms to
12,000.5 ms over 14.37 minutes. These values are device/model/binary/context/workload-specific.

### Phase 4 Android implementation

- Added the Gradle 8.11.1 multi-module project, AGP 8.9.1, Kotlin 2.3.0, Compose app,
  runtime-neutral engine API, and arm64 JNI/llama.cpp engine.
- Added device/model/chat/benchmark screens, model SHA-256 import support, device telemetry,
  streamed generation/cancellation contracts, and benchmark JSON export structures.
- Verified `./gradlew --no-daemon test assembleDebug assembleRelease`: 181 tasks completed
  successfully, including engine unit tests and the JNI CMake build.
- Produced a final 39,284,593-byte phase-aware debug APK with SHA-256
  `6228b85028a8f016ea6463f420da0a47e66264b87c3e3c8944922b66fcc87385`.
- Produced a final 28,836,295-byte phase-aware demo release APK with SHA-256
  `345c2d81fdf1a921b3617976ca9daaf2a82f5d0b3588c4a90c79719be2aa1a0b`.
- Verified both APKs with APK Signature Scheme v2.
- The phase-aware debug APK was installed and passed the complete functional device flow. The
  recorded release APK was installed and cold-launched; its full model flow was not repeated.

The release APK deliberately uses debug signing for the challenge demo. The phase-aware debug app
has passed on-device model import/hash/load, token generation, benchmark/export, cancellation,
and post-cancel reuse. The final supplement records complete source/JNI provenance,
1,760,477,184-byte VmHWM, and SwapFree across five runs/mode; exact hashes matched and optimized
decode was 10.6603 versus 6.0571 tok/s baseline. Artifact SHA-256:
`c335c71faed284c33202463582216b94b17ce9f5342a14d410fae76635a7993f`.

## Commit inventory through measured Phase 2 evidence

| Commit | Date | Challenge work |
|---|---|---|
| `2e22c9f` | 2026-07-20 | Repository scaffold, plan, docs, tools, and pinned llama.cpp |
| `27d3abd` | 2026-07-20 | Private-repository and cross-OS strategy |
| `e4d16f1` | 2026-07-20 | Gemma 4 E2B QAT and MTP-head manifest placeholders |
| `4fb366d` | 2026-07-20 | Sequential workstream gate rule |
| `0560c0a` | 2026-07-20 | Suite runner, summarizer, and Vulkan investigation |
| `0fcd0ea` | 2026-07-20 | CPU bring-up, verified Llama hash, coherent smoke result |
| `7be238f` | 2026-07-22 | SPIR-V CMake shim and Windows Vulkan blocker evidence |
| `964eeb3` | 2026-07-22 | Windows-to-Linux handoff |
| `1220d9a` | 2026-07-22 | Preliminary CPU baseline and harness fixes |
| `4462c70` | 2026-07-22 | Baseline documentation state recorded by official run |
| `8b464b7` | 2026-07-23 | Official Linux CPU/Vulkan baseline and twelve raw results |
| `54aabbd` | 2026-07-23 | Phase 2 profiling, affinity evidence, and bottleneck note |

## Existing evidence artifacts

| Artifact | Status |
|---|---|
| `benchmarks/results/raw/20260723-135809-phase1-baseline/` | Committed, 12 non-dry-run JSON files |
| `docs/baseline-results.md` | Committed official Phase 1 interpretation |
| `benchmarks/results/profiles/20260723-105825/` | Committed Phase 2 profile/affinity evidence |
| `docs/bottleneck-note.md` | Committed Phase 2 mechanism and workstream verdict |
| `benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json` | Native-MTP feasibility failure; SHA-256 `81e923…e58932` |
| `docs/phase0-report.md` | Committed build/deploy/smoke evidence |
| `docs/vulkan-build-notes.md` | Committed Linux recipe and capability evidence |
| Debug/release APKs | Built and v2-signed; exact hashes recorded in reproducibility docs |
| Root `LICENSE` | Apache License 2.0 present |

## In progress — not yet challenge results

The following must be added to this file only after corresponding evidence exists:

- [x] exact compact Gemma target file, quantization, size, source URL, and SHA-256 in the manifest;
- [x] exact matching MTP-head file, compatibility, size, source URL, and SHA-256 in the manifest;
- [x] end-to-end MTP-off/on harness validation and dry run;
- [x] suite-file and canonical per-prompt SHA-256 provenance in the harness artifact;
- [x] on-device model deployment and size/hash preflight;
- [x] native-MTP feasibility artifact, exact-output correctness, and failed gate verdict;
- [x] zero-weight n-gram fallback suite, runner, gates, tests, and dry-run validation;
- [x] zero-weight n-gram end-to-end device result bundle retained as rejection evidence;
- [x] phase-aware policy implementation and local isolation/profile-staleness proof;
- [x] target-token counts and exact output-hash correctness artifacts from the measured run;
- [x] sustained-run evidence (14.37 minutes, 15 samples/mode);
- [x] Android debug/release builds, APK hashes, and debug launch/JNI smoke;
- [x] Android model-load/generation, cancellation, benchmark, and export smokes;
- [x] headline chart sourced from the immutable final bundle;
- [ ] screenshots and demo video URL;
- [ ] public repository state and GitHub Release URL;
- [ ] fresh-clone reproduction record;
- [ ] Devpost submission URL and timestamp.

No value should be filled from an exploratory console log when the final protocol requires a raw
artifact.

## Third-party boundary

The challenge repository pins third-party submodules but does not claim their pre-existing code as
new work:

- llama.cpp remains a separate upstream project and license.
- Vulkan-Headers remains a Khronos submodule and license.
- Model weights are not redistributed by this repository and retain their model-specific terms.

The local llama-bench phase-thread patch compiles but remains a challenge-repository patch until
device evidence justifies any upstream proposal.
