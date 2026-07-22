# Handoff — Windows → Linux

Written 22 Jul 2026. Read this first when picking the project up on the Linux side of the dual boot.
Companion docs: [PLAN.md](PLAN.md) (strategy + checklists), [phase0-report.md](phase0-report.md) (build/deploy detail), [vulkan-build-notes.md](vulkan-build-notes.md) (Vulkan blocker analysis), [benchmark-methodology.md](benchmark-methodology.md).

---

## 1. Where the project stands

| Phase | State |
|---|---|
| 0 — environment, repo, first build | **DONE** (exit criterion met: model streams tokens on device) |
| 1 — baseline harness + numbers | **Tooling done; CPU baseline in progress on Windows; Vulkan blocked** |
| 2 — profiling / bottleneck note | not started |
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

**Uncommitted at time of writing** (fixes from the live benchmark run — commit before switching):
`tools/run_suite.py`, `benchmarks/suites/README.md`, `docs/benchmark-methodology.md`

**Not in git, and intentionally so:**
- `build-android-cpu/`, `build-android-vulkan/` — Windows-built, **worthless on Linux, do not copy**
- `benchmarks/results/raw/` — git-ignored; see §4 about preserving Phase 1 results
- Model weights — live on the **exFAT drive at `D:/models/`** (`Llama-3.2-1B-Instruct-Q4_0.gguf`, 773,025,920 B, sha256 `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`)

**On the device** at `/data/local/tmp/llama-edge/`: `llama-completion`, `llama-bench` (Windows-built CPU-only, static) + `models/Llama-3.2-1B-Instruct-Q4_0.gguf`. These will be **replaced** by Linux-built binaries.

## 4. Migration: clone from GitHub (not a folder copy)

**Do this, in order:**

1. **On Windows, before rebooting:** commit + push the uncommitted files above. Also commit the Phase 1 raw results — they are small JSON and are evidence:
   ```bash
   git add -f benchmarks/results/raw/<timestamp>-phase1-baseline
   git commit -m "Phase 1 preliminary CPU baseline (Windows-built binaries)"
   git push
   ```
2. **On Linux:** `git clone --recurse-submodules` into your **home dir (ext4)** — never onto the exFAT drive (no exec bits, no symlinks, no journaling).
3. **Models:** already on the exFAT drive; just mount it and `export LLAMA_EDGE_MODELS=/path/to/mount/models`. No re-download, no copying.
4. **Do not** copy `build-android-*/` or the Windows `C:\Tools\w64devkit` — Linux has its own toolchain.

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

## 8. Numbers so far (preliminary — Windows-built binaries)

Llama 3.2 1B Q4_0, 2× A78 pinned, pp64/tg32, 2 reps:
**prefill 45.08 ± 0.25 tok/s · decode 12.16 ± 0.15 tok/s** (build `178a6c4`)

The fuller CPU thread sweep (t=2/4/6/8 × pp512/tg128 + pg512+128) was running on Windows at handoff time; see `docs/baseline-results.md` if present. **Re-run it on Linux-built binaries before treating any of it as the baseline.**

## 9. Next actions, in order

1. Finish Linux setup (§5) and rebuild CPU; re-run baseline suite → official baseline
2. **Build Vulkan and answer the key question: does the PowerVR BXM driver run llama.cpp's Vulkan backend at all, and is it faster or slower than 2×A78?** This is the highest-information experiment in the project
3. Phase 2: simpleperf + Perfetto on the slower/winning path; run `test-backend-ops` on Vulkan (failures are themselves reportable findings); write `docs/bottleneck-note.md`
4. **Gate G1** — pick exactly one workstream (A adaptive routing / B Vulkan tuning / C big.LITTLE threading / D adaptive MTP). Evidence decides; see PLAN.md decision table
5. Then Phase 3 implementation, with `baseline` vs `optimized` builds differing only in that change

## 10. Admin still outstanding

- [ ] Register on Devpost (Mobile AI track) — not done yet
- [ ] Flip repo private → public before 12 Aug submission
- [ ] Fork/patch-host for llama.cpp changes — deferred to Phase 3 (see PLAN.md; forks can't be private, so use a standalone private repo if still private then)
