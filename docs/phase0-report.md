# Phase 0 Exit Report — CPU cross-compile, model bring-up, device smoke test

**Date:** 2026-07-20
**Device:** Redmi Note 14 5G (`24094RAD4I`, ADB serial `8DYTMRKF755TOBZD`) — MediaTek Dimensity 7025 (MT6855), 2× Cortex-A78 (cpu6/7 @ 2.5 GHz) + 6× Cortex-A55 (cpu0–5 @ 2.0 GHz), arm64-v8a, Android 16, 5.6 GB RAM.
**llama.cpp:** pinned upstream `178a6c4` (b10069), ggml version 0.17.0. **No changes made under `third_party/llama.cpp`.**
**Toolchain:** NDK `28.2.13676358` (r28c), SDK CMake 3.22.1 + bundled ninja 1.10.2, Ninja generator.

## Exit criterion: **MET**
The Llama 3.2 1B Q4_0 model streams coherent tokens on the Redmi from the adb shell, and `llama-bench` produces stable pp/tg numbers. See outputs below.

---

## 1. Build

### Feature-flag decision
The device is Cortex-A78/A55 = **armv8.2-a + dotprod + fp16, with NO i8mm, NO SVE, NO SME**. The upstream `docs/android.md` suggests `-march=armv8.7a`, but that would let the compiler emit i8mm/SVE instructions this device cannot execute (SIGILL risk on the non-runtime-dispatched paths). Instead we set `-march=armv8.2-a+dotprod+fp16`, which exactly matches the silicon. CMake's ARM feature probe confirmed the match:

```
HAVE_DOTPROD - Success
HAVE_FP16_VECTOR_ARITHMETIC - Success
HAVE_SVE - Failed
HAVE_MATMUL_INT8 (i8mm) - Failed
HAVE_SME - Failed
```

At runtime the binary reports: `CPU : NEON = 1 | ARM_FMA = 1 | FP16_VA = 1 | DOTPROD = 1 | REPACK = 1`.

### Exact CMake configure command (recommended / clean)
Run from `C:\Projects\llama-edge-android` (Git Bash). This builds the two needed targets without pulling in the server web UI (see the `llama-cli` note in §5):

```bash
ANDROID_NDK="C:/Users/win-home/AppData/Local/Android/Sdk/ndk/28.2.13676358"
CMAKE="C:/Users/win-home/AppData/Local/Android/Sdk/cmake/3.22.1/bin/cmake.exe"
NINJA="C:/Users/win-home/AppData/Local/Android/Sdk/cmake/3.22.1/bin/ninja.exe"

"$CMAKE" -S third_party/llama.cpp -B build-android-cpu -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$ANDROID_NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_OPENMP=OFF \
  -DGGML_LLAMAFILE=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+fp16" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16"
```

- `GGML_OPENMP=OFF` — required per upstream `docs/android.md` (NDK OpenMP must be installed by CMake as a dependency, unsupported).
- `GGML_LLAMAFILE=OFF` — llamafile does not support Android (upstream note).
- `BUILD_SHARED_LIBS=OFF` — fully static binaries, so **no `.so` files need to be pushed** to the device.
- `LLAMA_CURL=OFF` — avoids the libcurl host dependency (models are side-loaded).

### Build command
```bash
"$CMAKE" --build build-android-cpu --config Release -j 8 --target llama-completion llama-bench
```

### Build time
~3.5 min wall (~205 s) with `-j 8` on the shared dev CPU (concurrent Vulkan build running). Configure ~30 s.

### Binaries (`build-android-cpu/bin/`)
| Binary | Unstripped | Stripped (pushed) | Type |
|---|---:|---:|---|
| `llama-completion` | 124,672,144 B (~119 MB) | 6,700,464 B (~6.4 MB) | ELF64 aarch64 PIE, Android 28, NDK r28c |
| `llama-bench` | 123,272,920 B (~118 MB) | 4,662,792 B (~4.4 MB) | ELF64 aarch64 PIE, Android 28, NDK r28c |

Unstripped binaries carry `debug_info`; stripped copies (`*.stripped`) were made with the NDK `llvm-strip` and pushed to the device.

---

## 2. Model
- File: `D:\models\Llama-3.2-1B-Instruct-Q4_0.gguf`
- Source: `https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_0.gguf`
- Size: **773,025,920 bytes** (0.72 GiB); GGUF, llama arch, 1.24 B params, Q4_0 (729.75 MiB tensor size).
- **sha256: `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`**
- `models/manifest.json` entry `llama-3.2-1b-instruct-q4_0` updated with this sha256.

---

## 3. Device deployment (adb)
Device free space before push: `/data` 55 GB available of 109 GB (49% used) — ample.

```powershell
adb shell mkdir -p /data/local/tmp/llama-edge/models
adb push build-android-cpu\bin\llama-completion.stripped /data/local/tmp/llama-edge/llama-completion
adb push build-android-cpu\bin\llama-bench.stripped      /data/local/tmp/llama-edge/llama-bench
adb shell chmod 755 /data/local/tmp/llama-edge/llama-completion /data/local/tmp/llama-edge/llama-bench
adb push D:\models\Llama-3.2-1B-Instruct-Q4_0.gguf /data/local/tmp/llama-edge/models/
```
All three pushes succeeded (model at 27.5 MB/s, 26.8 s). Static binaries — no `.so` pushed.

---

## 4. Smoke test

### Generation (llama-completion, greedy, -n 32)
```
adb shell "cd /data/local/tmp/llama-edge && taskset c0 ./llama-completion \
  -m models/Llama-3.2-1B-Instruct-Q4_0.gguf \
  -p 'The three primary colors are' -n 32 --temp 0 -no-cnv -c 512 -t 2 --seed 1"
```
Output:
> The three primary colors are red, blue, and yellow. These colors are combined in different ways to create a wide range of colors. The primary colors are the base colors that cannot be

Perf (this run, 2× A78): load 4.32 s, prompt eval **17.69 tok/s** (6 tok), generation (tg) **11.92 tok/s** (31 tok).

### llama-bench quick check (-p 64 -n 32, 2 reps, 2× A78)
```
adb shell "cd /data/local/tmp/llama-edge && taskset c0 ./llama-bench \
  -m models/Llama-3.2-1B-Instruct-Q4_0.gguf -p 64 -n 32 -r 2 -t 2"
```
| model | size | params | backend | threads | test | t/s |
|---|---:|---:|---|---:|---:|---:|
| llama 1B Q4_0 | 729.75 MiB | 1.24 B | CPU | 2 | pp64 | **45.08 ± 0.25** |
| llama 1B Q4_0 | 729.75 MiB | 1.24 B | CPU | 2 | tg32 | **12.16 ± 0.15** |

`build: 178a6c4 (1)` — matches the pinned commit.

---

## 5. Deviations, findings & warnings

1. **`llama-cli` could not be built on this Windows host → used `llama-completion` instead (documented equivalent).**
   At commit `178a6c4`, upstream refactored the old text-generation `llama-cli` (`main.cpp`) into **`llama-completion`** (`tools/completion/`), and the *new* `llama-cli` (`tools/cli/`) is a **server-based chat client** that links `llama-server-impl` and therefore the server **web UI**. Building the web UI runs a host-compiled embed tool (`tools/ui/embed.cpp`). This machine has **no usable host C++ compiler** (no MSVC, no mingw g++/clang++, no standalone LLVM; only the NDK cross-clang, which lacks Windows host libc headers → `fatal error: 'inttypes.h' file not found`). CMake even mis-selected the NDK clang as `HOST_CXX_COMPILER`. `llama-completion` depends only on `llama` + `llama-common` (no server/UI), is the direct functional successor of the classic `llama-cli`, and satisfies the "stream tokens from the shell" exit criterion. **Note:** the sibling Vulkan build dir is configured `LLAMA_BUILD_SERVER=ON` / `LLAMA_BUILD_UI=ON` with the same NDK-clang-as-host mis-selection, so it will hit the identical UI-embed failure if it tries to link `llama-cli`. To build the real `llama-cli` later, install a host C++ toolchain and pass `-DLLAMA_BUILD_SERVER=ON -DHOST_CXX_COMPILER=<path-to-host-g++-or-clang++>`.

2. **Context size is critical on this 5.6 GB device.** The first smoke run used llama.cpp's default `n_ctx = 126976` (the model's full 128K training context). The resulting KV-cache allocation drove the device into memory thrashing (MemAvailable ~1.5 GB): load took 48 s and generation crawled at **0.26 tok/s**. Setting `-c 512` fixed it entirely (tg jumped to ~12 tok/s). **Action for Phase 1:** always pass an explicit small `-c` (e.g. 512/2048/4096) for benchmarking; never rely on the 128K default on this device.

3. **big.LITTLE pinning matters.** Runs are pinned to the two A78 cores with toybox `taskset c0` (hex mask, cores 6+7; note toybox taskset does **not** accept `-c 6,7` cpu-list syntax on this device — use the bare hex mask). The adb shell's own cpuset is `/` (unrestricted), so slowness was memory, not cgroup confinement. Thread/core-policy sweeps are a Phase 1 task.

4. **Harmless configure warnings:** `ccache not found`; `GGML_COMPILER_SUPPORTS_FP16_FORMAT_I3E - Failed` (expected on this clang, non-fatal); OpenSSL-not-found (only relevant to the server, which we don't build).

5. Git working tree left dirty for review (new `build-android-cpu/`, updated `models/manifest.json`, this report). **No commits or pushes made.**
