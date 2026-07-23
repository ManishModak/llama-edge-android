# Handoff — Windows → Linux

> **✅ MIGRATION COMPLETE (23 Jul 2026).** The Linux environment is set up, both CPU and
> **Vulkan** variants build, and both are deployed to the device. The Vulkan blocker that
> forced this migration is resolved — see [vulkan-build-notes.md](vulkan-build-notes.md).
> Sections 4–6 below are now historical; §7 gotchas and §9 next actions remain live.
>
> Linux env: CachyOS (Arch), GCC 16.1.1, CMake 4.3.4, ninja 1.13.1,
> NDK r28c at `~/Android/Sdk/ndk/28.2.13676358`, models at
> `/run/media/manishm/T7_Shield/models/`.

Written 22 Jul 2026, final at commit `1220d9a`. Read this first when picking the project up on the Linux side of the dual boot.
**Everything is committed and pushed — the working tree is clean. Nothing needs hand-copying; clone and go.**
Companion docs: [PLAN.md](PLAN.md) (strategy + checklists), [phase0-report.md](phase0-report.md) (build/deploy detail), [vulkan-build-notes.md](vulkan-build-notes.md) (Vulkan blocker analysis), [benchmark-methodology.md](benchmark-methodology.md).

---

## 1. Where the project stands

| Phase | State |
|---|---|
| 0 — environment, repo, first build | **DONE** (exit criterion met: model streams tokens on device) |
| 1 — baseline harness + numbers | **DONE** — official Linux-built baseline, 12/12 cases (9 CPU + 3 Vulkan), see [baseline-results.md](baseline-results.md) |
| 2 — profiling / bottleneck note | **NEXT** |
| 3 — the one optimization | not started (Gate G1 selects it) |
| 4 — Android app | not started |
| 5 — evidence, docs, demo, submit | not started |

Deadline **14 Aug 2026 4:00 PM PDT**; submission target **12 Aug**. Repo is **private** — must be flipped public before submitting.

## 2. Verified facts (do not re-derive)

**Device — Redmi Note 14 5G**, adb serial `8DYTMRKF755TOBZD`
- MediaTek Dimensity 7025 (MT6855); 2× Cortex-A78 (cores **6,7**) + 6× Cortex-A55 (cores 0–5)
- SIMD: **dotprod + fp16**; **no i8mm, no SVE** → build with `-march=armv8.2-a+dotprod+fp16`
- GPU **PowerVR B-Series BXM-8-256**, Vulkan **1.3**, driver `25.1@6715691`
- **5.6 GB usable RAM** (memory pressure is real and load-bearing — see gotchas)
- Android 16, arm64-v8a, unrooted; ~55 GB free storage

**Challenge:** Mobile AI track. Judging Tech 40 / WOW 25 / Impact 20 / DX 15. Needs public repo, MIT/Apache-2.0, build docs, optional <3 min video.

**llama.cpp submodule** pinned at `178a6c4` (upstream build **b10069**, 19 Jul 2026), shallow clone. Gemma 4 native MTP landed upstream in b9549, so **MTP is available at this pin**.

## 3. What exists right now

**Committed & pushed** (7 commits, HEAD `7be238f`):
- Repo scaffold, Apache-2.0, PLAN.md, docs templates
- `tools/device_snapshot.py`, `tools/run_suite.py`, `tools/summarize_results.py`
- `benchmarks/suites/phase1-baseline.json` + schema README
- `tools/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake` (Vulkan header shim — **works, keep it**)
- `models/manifest.json` with Llama 3.2 1B sha256 + Gemma 4 E2B QAT entries
- `docs/phase0-report.md`, `docs/vulkan-build-notes.md`

- `docs/baseline-results.md` + the Phase 1 raw result JSONs (force-added, git-ignore notwithstanding — they are evidence)

**Not in git, and intentionally so:**
- `build-android-cpu/`, `build-android-vulkan/` — Windows-built, **worthless on Linux, do not copy**
- Future `benchmarks/results/raw/` runs — git-ignored by default; force-add the ones that matter
- Model weights — live on the **exFAT drive at `D:/models/`** (`Llama-3.2-1B-Instruct-Q4_0.gguf`, 773,025,920 B, sha256 `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`)

**On the device** at `/data/local/tmp/llama-edge/`: `llama-completion`, `llama-bench` (Windows-built CPU-only, static) + `models/Llama-3.2-1B-Instruct-Q4_0.gguf`. These will be **replaced** by Linux-built binaries.

## 4. Migration: clone from GitHub (not a folder copy)

**Windows side is finished — nothing left to push.** On Linux:

1. `git clone --recurse-submodules https://github.com/ManishModak/llama-edge-android.git` into your **home dir (ext4)** — never onto the exFAT drive (no exec bits, no symlinks, no journaling).
2. **Models:** already on the exFAT drive; mount it and `export LLAMA_EDGE_MODELS=/path/to/mount/models`. No re-download, no copying.
3. **Do not** copy `build-android-*/` or the Windows `C:\Tools\w64devkit` — Linux has its own toolchain.

**Why clone, not copy:** git is the sync mechanism between your two OSes for the rest of the project; the only things worth moving by drive are the multi-GB model files, and those already live on the shared drive. A folder copy would drag along dead Windows build artifacts and lose git's identity of what's pushed.

## 5. Linux setup checklist

- [ ] Install: `git`, `cmake` (≥3.22), `ninja-build`, `python3`, `android-sdk-platform-tools` (adb), a host C++ compiler (`build-essential`)
- [ ] Android NDK **r28** (same major as Windows side: 28.2.13676358) — SDK manager or the standalone zip
- [ ] udev rule for the phone (Xiaomi vendor id `2717`), then `adb devices` → confirm serial `8DYTMRKF755TOBZD`
- [ ] `git clone --recurse-submodules https://github.com/ManishModak/llama-edge-android.git`
- [ ] Verify submodule pin: `git -C third_party/llama.cpp rev-parse --short HEAD` → `178a6c4`
- [ ] `export LLAMA_EDGE_MODELS=<exfat-mount>/models`
- [ ] Rebuild **CPU** variant (see §6), push to device, sanity-run
- [ ] Build **Vulkan** variant — the whole reason for the switch
- [ ] Re-run the baseline suite with Linux-built binaries → **this becomes the official baseline**

## 6. Build commands (translate to Linux paths)

CPU variant that worked on Windows (flags are what matter):
```
cmake -G Ninja -B build-android-cpu -S third_party/llama.cpp \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DGGML_OPENMP=OFF -DGGML_LLAMAFILE=OFF -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+fp16" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16"
cmake --build build-android-cpu --target llama-bench llama-completion -j
```

Vulkan variant — same as above **plus**:
```
  -DGGML_VULKAN=ON \
  -DVulkan_GLSLC_EXECUTABLE=$NDK/shader-tools/linux-x86_64/glslc \
  -DSPIRV-Headers_DIR=$PWD/tools/cmake/SPIRV-Headers
```
On Linux the host `vulkan-shaders-gen` builds **and runs** with the system g++ — no w64devkit, no Smart App Control. Expect this to simply work. Name the output `llama-bench-vulkan` when pushing (the suite's vulkan cases expect that binary name; override `binary`/`binDir` per case otherwise).

Deploy:
```
adb push build-android-cpu/bin/llama-bench /data/local/tmp/llama-edge/
adb shell chmod +x /data/local/tmp/llama-edge/llama-bench
```

## 7. Gotchas already paid for (don't rediscover these)

1. **`llama-cli` was renamed.** At this pin, classic text generation is **`llama-completion`**; the new `llama-cli` is a server-based chat client that embeds a web UI and needs extra host tooling. Use `llama-completion`.
2. **Always pass an explicit small `-c`.** Default context is 128K; on 5.6 GB RAM it thrashes → **0.26 tok/s and 48 s load**. With `-c 512` it's ~12 tok/s. (This memory sensitivity is itself good evidence for the mobile-constraints story — keep it in the report.)
3. **`taskset` on this device is toybox**: it wants a bare hex mask, e.g. `taskset c0` = the two A78 cores. `-c 6,7` does not work.
4. **`llama-bench` at this pin has no seed and no context-size flag.** pp/tg workloads are synthetic; `seed`/`contextSize` are metadata only. Determinism/correctness checks belong to `llama-completion` runs. `-d/--n-depth` exists if you need prefilled-depth benchmarks.
5. **Vulkan needs a host-side shader generator** — that's the whole Windows blocker (Smart App Control refuses to execute freshly compiled unsigned .exe). Non-issue on Linux.
6. **Binaries from different toolchains are not comparable.** Every A/B must use binaries built by the same toolchain. Windows numbers below are a sanity reference only.
7. **An Android emulator is also attached on the Linux box.** Bare `adb shell` fails with `more than one device/emulator`. Always pin the phone: `export ANDROID_SERIAL=8DYTMRKF755TOBZD` (adb honours it natively), or pass `--serial` to `run_suite.py`. Note `tools/device_snapshot.py` has **no** `--serial` flag — it needs the env var, and silently writes an all-empty snapshot if it can't reach a unique device. Check the snapshot is populated before trusting a run.
8. **Vulkan cross-compile needs two headers the NDK doesn't provide.** `vulkan.hpp` (vendored: `third_party/Vulkan-Headers` @ `v1.4.350`) and `spirv/unified1/spirv.hpp` (present in the NDK but never propagated to the `ggml-vulkan` target — inject with `-isystem`). Full recipe in [vulkan-build-notes.md](vulkan-build-notes.md).
9. **zsh does not word-split unquoted variables.** `D="-s $SERIAL"; adb $D shell …` fails on this box (works in bash). Inline the flags or use `${=D}`.
10. **Piping a build to `tail` masks its exit code.** `cmake --build … | tail -40` reports tail's status, so a failed ninja build looks like success. Redirect to a log and check `$?` instead.

## 8. Numbers so far (SUPERSEDED — Windows-built binaries)

> **⚠ Superseded 23 Jul 2026.** The official baseline is now the Linux-built 12-case run in
> [baseline-results.md](baseline-results.md). Two findings below did **not** reproduce:
> **"t=8 collapses unpredictably" is false** (Linux: 80.04 ± 1.34, the *best* prefill result),
> and t=2 prefill is ~46.5 not 66.11. What did reproduce: the t=6 decode optimum (14.16),
> the t=4 < t=2 decode anomaly, and the ~17 % pg shortfall.
> Vulkan is no longer blocked — it builds, runs, and **loses to CPU on every workload**
> (prefill 2.0×, decode 9.4×, combined 5.4×).

Llama 3.2 1B Q4_0, `llama-bench` @ `178a6c4`, 5 reps/case, 120 s cooldowns, 9/9 cases clean:

| threads | pp512 tok/s | tg128 tok/s |
|---:|---:|---:|
| 2 | 66.11 ± 6.77 | 12.80 ± 0.16 |
| 4 | 63.41 ± 2.65 | 11.19 ± 0.53 |
| **6** | **68.20 ± 0.77** | **14.16 ± 0.20** |
| 8 | 58.62 ± 26.74 | 9.48 ± 2.31 |

pg512+128 @ t=6: **31.98 ± 4.58 tok/s**. Thermal status never left NONE; skin 36.5 → 42.2 °C.
Full report + 6 observations: `docs/baseline-results.md`. Raw JSON: `benchmarks/results/raw/20260722-210643-phase1-baseline/`.

Findings that will shape Gate G1:
- **t=6 optimal and most stable** (~1 % rel. stddev vs 46 % at t=8)
- **Non-monotonic scaling — t=4 is worse than t=2**: big.LITTLE straggler effect (A55 threads stall A78s at ggml's per-op barrier) → direct evidence for **workstream C**
- **t=8 collapses unpredictably** (reps 74.8 / 78.9 / 78.1 / **19.6** / 41.7)
- **Decode is memory-bandwidth bound**; prefill is 4.8× decode
- **pg runs 17 % below naive pp/tg composition** — KV-depth cost is real
- **DVFS boost decay visible within a single 50 s case** at only +0.7 °C — not thermal; sustained-run design must account for it

⚠ **Re-run this suite with Linux-built binaries before treating any of it as the official A/B baseline.**

Benchmark hygiene notes: battery was 99 % and USB-charging (unavoidable with adb as transport), so battery-delta is meaningless and temps are mildly pessimistic. Airplane mode can't be toggled over adb. `svc power stayon usb` is still set on the device — clear with `adb shell svc power stayon false` when done benchmarking.

## 9. Next actions, in order

1. ~~Finish Linux setup (§5) and rebuild CPU; re-run baseline suite~~ → **DONE**, official baseline in [baseline-results.md](baseline-results.md)
2. ~~**Build Vulkan and answer the key question**~~ → **ANSWERED.** The BXM driver *does* run llama.cpp's Vulkan backend, and it **loses to CPU on every workload** — prefill 2.0×, decode 9.4×, combined 5.4×. Cause is in the device flags: `int dot: 0`, `matrix cores: none`, 16 KB shared memory
3. ~~Phase 2~~ → **DONE**, see [bottleneck-note.md](bottleneck-note.md). Headline: **decode is DRAM-bound at 65–75 % of LPDDR4X peak and six A55s alone match the best full-SoC decode**, so no threading policy can make it faster. simpleperf is unusable on this device (kernel refuses `perf_event_open` entirely); substituted `/proc` sampling
4. **Gate G1 (31 Jul) — NEXT.** Evidence now points at **D, draft-model-free** (native MTP / prompt-lookup / self-speculation). **C is refuted for throughput**; A ships an always-off switch; B is dead. Note PLAN.md's D sizing assumes 5.6 GB — measured `MemAvailable` is **1.84 GB**, so a 3B+1B pair does not fit
5. Then Phase 3 implementation, with `baseline` vs `optimized` builds differing only in that change

## 10. Admin still outstanding

- [ ] Register on Devpost (Mobile AI track) — not done yet
- [ ] Flip repo private → public before 12 Aug submission
- [ ] Fork/patch-host for llama.cpp changes — deferred to Phase 3 (see PLAN.md; forks can't be private, so use a standalone private repo if still private then)
