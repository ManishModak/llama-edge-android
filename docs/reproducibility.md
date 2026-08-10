# Reproducibility

This document reproduces the committed CPU/Vulkan baseline and the measured native-MTP feasibility
failure. It also identifies evidence that does not exist yet, so that feasibility run cannot be
mistaken for a final optimized result.

## Reproduction target

| Item | Exact reference |
|---|---|
| Project evidence snapshot | `54aabbde5b9c31340f685ba6075a00222b8908f8` |
| Project commit recorded in official Phase 1 JSON | `4462c70587f9cdd6d00b67b5964cac060014c7a3` |
| llama.cpp | `178a6c44937154dc4c4eff0d166f4a044c4fceba` (upstream build b10069) |
| Vulkan-Headers | `8864cdc896bbc2a9b6eb36b3218fc9ef57908d77` (`v1.4.350`) |
| Official result bundle | `benchmarks/results/raw/20260723-135809-phase1-baseline/` |
| Native-MTP feasibility artifact | `benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json` |
| Native-MTP artifact SHA-256 | `81e92389437bfe9b02ab7c0b9d5dd41a29f5308bfbbd4711692b4ff114e58932` |
| Device | Redmi Note 14 5G (`24094RAD4I`), MediaTek MT6855, Android 16 |
| Model | Llama 3.2 1B Instruct, Q4_0, 773,025,920 bytes |
| Model SHA-256 | `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8` |

The result JSONs record `4462c70` because that was `HEAD` while the suite ran. Commit `8b464b7`
then added the official result bundle and Linux/Vulkan documentation; `54aabbd` added Phase 2
profiling evidence. Those later commits do not retroactively change the recorded binary or model.

## Recorded toolchain

The official Linux-built baseline documents:

| Component | Recorded value |
|---|---|
| Host OS | CachyOS/Arch Linux |
| Host compiler | GCC 16.1.1 |
| Android NDK | `28.2.13676358` (r28c) |
| CMake | 4.3.4 |
| Ninja | 1.13.1 |
| Android ABI/API | `arm64-v8a` / `android-28` |
| ISA flags | `-march=armv8.2-a+dotprod+fp16` |
| Linkage | Static (`BUILD_SHARED_LIBS=OFF`) |
| JDK used for Android build | OpenJDK 21.0.11; Java/Kotlin bytecode target 17 |
| Gradle wrapper | 8.11.1 |
| Android Gradle Plugin | 8.9.1 |
| Kotlin | 2.3.0 |
| App SDK levels | compile 36, target 36, min 28 |
| App native build | NDK `28.2.13676358`, SDK CMake 3.22.1, arm64-v8a |

These app values are verified by debug and release builds. The release APK intentionally uses the
debug signing key for the challenge demo; no store-distribution signing path is claimed.

## Host prerequisites

- Git with submodule support
- Python 3.12 or newer; the committed tools use only the standard library
- JDK 21 for the verified Gradle build
- Android platform-tools (`adb`)
- CMake and Ninja
- A host C++ compiler for Vulkan shader generation
- Android NDK `28.2.13676358`
- `curl` and `sha256sum`, or equivalent download/hash tools
- An arm64 Android device with USB debugging enabled

The exact figures above are the recorded environment, not a claim that every newer host toolchain
is bit-for-bit equivalent. Do not compare binaries built by different toolchains in an A/B.

## 1. Clean checkout and pin verification

The repository is private at the time of this documentation snapshot. Cloning requires access
until it is made public for submission.

```bash
git clone --depth 1 --shallow-submodules \
  --branch agent/phase-aware-autotuner --recurse-submodules \
  https://github.com/ManishModak/llama-edge-android.git
cd llama-edge-android
git submodule update --init --recursive
git status --short
git -C third_party/llama.cpp rev-parse HEAD
git -C third_party/Vulkan-Headers rev-parse HEAD
```

Expected:

```text
# git status --short emits nothing
178a6c44937154dc4c4eff0d166f4a044c4fceba
8864cdc896bbc2a9b6eb36b3218fc9ef57908d77
```

## 2. Model download and integrity check

Model weights are intentionally excluded from Git.

```bash
TASK_MODEL_DIR=/absolute/path/to/models
mkdir -p "$TASK_MODEL_DIR"
curl -L \
  -o "$TASK_MODEL_DIR/Llama-3.2-1B-Instruct-Q4_0.gguf" \
  https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_0.gguf
sha256sum "$TASK_MODEL_DIR/Llama-3.2-1B-Instruct-Q4_0.gguf"
stat -c '%s' "$TASK_MODEL_DIR/Llama-3.2-1B-Instruct-Q4_0.gguf"
```

Expected:

```text
fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8  Llama-3.2-1B-Instruct-Q4_0.gguf
773025920
```

### Phase 3 candidate inputs

The manifest now records the selected small target and matching head. These hashes identify the
inputs; they do not prove that the pair fits or performs well on the phone.

```bash
TASK_MODEL_DIR=/absolute/path/to/models
mkdir -p "$TASK_MODEL_DIR"
curl -L \
  -o "$TASK_MODEL_DIR/gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf" \
  https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf
curl -L \
  -o "$TASK_MODEL_DIR/mtp-gemma-4-E2B-it.gguf" \
  https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/mtp-gemma-4-E2B-it.gguf
sha256sum \
  "$TASK_MODEL_DIR/gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf" \
  "$TASK_MODEL_DIR/mtp-gemma-4-E2B-it.gguf"
```

Expected:

```text
0a5bbc20f91f92da96ab4870fa71b356c45b8500a7b8b9c3e0eb48359b72da28  gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf
586f2460b909008640981ec34060aa864e03c144fbabfb3173c4335087e4aae0  mtp-gemma-4-E2B-it.gguf
```

Expected byte sizes are 2,186,186,784 and 59,235,648 respectively. The runner rechecks both size
and SHA-256 on the device before a measured session. Both checks passed in the recorded feasibility
run. The target alone is larger than the 1.84 GB `MemAvailable` observed during the earlier Llama
session; both feasibility modes subsequently loaded, but both reduced `SwapFree`, so the no-swap
gate failed.

## 3. CPU build

Use an absolute NDK path. The command below matches the recorded Linux configuration.

```bash
TASK_NDK=/absolute/path/to/Android/Sdk/ndk/28.2.13676358

cmake -G Ninja -B build-android-cpu -S third_party/llama.cpp \
  -DCMAKE_TOOLCHAIN_FILE="$TASK_NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_OPENMP=OFF \
  -DGGML_LLAMAFILE=OFF \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+fp16" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16"

cmake --build build-android-cpu \
  --target llama-bench llama-completion \
  --parallel 12
```

The classic text-generation executable at this pin is `llama-completion`; the current
`llama-cli` is a server client and is not used by this project.

## 4. Vulkan build

The NDK lacks the C++ Vulkan header used by llama.cpp, and its SPIR-V headers are not propagated to
the target. The pinned Vulkan-Headers submodule and committed CMake shim solve those two
cross-compile gaps without modifying llama.cpp.

```bash
TASK_NDK=/absolute/path/to/Android/Sdk/ndk/28.2.13676358
TASK_SPV="$TASK_NDK/sources/third_party/shaderc/third_party/spirv-tools/external/spirv-headers/include"

cmake -G Ninja -B build-android-vulkan -S third_party/llama.cpp \
  -DCMAKE_TOOLCHAIN_FILE="$TASK_NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_OPENMP=OFF \
  -DGGML_LLAMAFILE=OFF \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+fp16" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16 -isystem $TASK_SPV" \
  -DGGML_VULKAN=ON \
  -DVulkan_GLSLC_EXECUTABLE="$TASK_NDK/shader-tools/linux-x86_64/glslc" \
  -DVulkan_INCLUDE_DIR="$PWD/third_party/Vulkan-Headers/include" \
  -DSPIRV-Headers_DIR="$PWD/tools/cmake/SPIRV-Headers"

cmake --build build-android-vulkan \
  --target llama-bench llama-completion \
  --parallel 12
```

More detail, including the historical Windows blocker, is in
[vulkan-build-notes.md](vulkan-build-notes.md).

## 5. Strip and deploy

Keep unstripped binaries locally for symbolization. The suite expects the Vulkan benchmark binary
to be named `llama-bench-vulkan`.

```bash
TASK_NDK=/absolute/path/to/Android/Sdk/ndk/28.2.13676358
TASK_STRIP="$TASK_NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
TASK_MODEL_DIR=/absolute/path/to/models

mkdir -p dist/cpu dist/vulkan
"$TASK_STRIP" -o dist/cpu/llama-bench build-android-cpu/bin/llama-bench
"$TASK_STRIP" -o dist/cpu/llama-completion build-android-cpu/bin/llama-completion
"$TASK_STRIP" -o dist/vulkan/llama-bench-vulkan build-android-vulkan/bin/llama-bench
"$TASK_STRIP" -o dist/vulkan/llama-completion-vulkan build-android-vulkan/bin/llama-completion

export ANDROID_SERIAL=<phone-serial>
adb shell mkdir -p /data/local/tmp/llama-edge/models
adb push dist/cpu/llama-bench /data/local/tmp/llama-edge/llama-bench
adb push dist/cpu/llama-completion /data/local/tmp/llama-edge/llama-completion
adb push dist/vulkan/llama-bench-vulkan /data/local/tmp/llama-edge/llama-bench-vulkan
adb push dist/vulkan/llama-completion-vulkan /data/local/tmp/llama-edge/llama-completion-vulkan
adb push "$TASK_MODEL_DIR/Llama-3.2-1B-Instruct-Q4_0.gguf" \
  /data/local/tmp/llama-edge/models/
adb shell chmod 755 \
  /data/local/tmp/llama-edge/llama-bench \
  /data/local/tmp/llama-edge/llama-completion \
  /data/local/tmp/llama-edge/llama-bench-vulkan \
  /data/local/tmp/llama-edge/llama-completion-vulkan
```

## 6. Apply the pinned llama.cpp patch series

The repository carries two patches against the pinned submodule: the measured MTP draft-path fix
and the phase-aware `llama-bench` `-tb` implementation. The helper checks or applies every patch in
lexical order and is idempotent:

```bash
python tools/apply_llama_patch.py --apply
python tools/apply_llama_patch.py
```

The second patch creates separate decode and batch threadpools, records `n_threads` and
`n_threads_batch` in JSON output, and lets the autotuner measure real phase pairs such as
`-t 6 -tb 8` instead of merely describing them in its report.

## 7. Build the Phase 3 server and validate its harness

The confirmed build reused the CPU CMake tree configured in section 3:

```bash
cmake -S third_party/llama.cpp \
  -B build-android-cpu \
  -DLLAMA_BUILD_SERVER=ON
cmake --build build-android-cpu \
  --target llama-server \
  --parallel 2
sha256sum build-android-cpu/bin/llama-server
```

The resulting unstripped binary is an Android API-28 arm64 ELF with SHA-256:

```text
41677f22d4bf9ec94270cb22b287f2e4356379c0cff7252e72d62739119b5743
```

CMake's UI asset fetch failed in the offline build environment, then the server built without
embedded UI. The Phase 3 runner uses `--no-webui`. The same unstripped binary was deployed and its
on-device SHA-256 matched the host artifact.

The deployment used these paths:

```bash
TASK_MODEL_DIR=/absolute/path/to/models
export ANDROID_SERIAL=<phone-serial>
adb shell mkdir -p /data/local/tmp/llama-edge/models
adb push build-android-cpu/bin/llama-server /data/local/tmp/llama-edge/llama-server
adb push "$TASK_MODEL_DIR/gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf" \
  /data/local/tmp/llama-edge/models/
adb push "$TASK_MODEL_DIR/mtp-gemma-4-E2B-it.gguf" \
  /data/local/tmp/llama-edge/models/
adb shell chmod 755 /data/local/tmp/llama-edge/llama-server
```

Validate the suite and preview all server flags without a device:

```bash
python tools/run_mtp_ab.py --validate-only
python tools/run_mtp_ab.py --dry-run --cooldown 0
```

The dry run writes a schema-version-1 placeholder. It is not measured evidence.

An equivalent command for the recorded one-repetition feasibility configuration is:

```bash
export ANDROID_SERIAL=<phone-serial>
python tools/run_mtp_ab.py benchmarks/suites/phase3-mtp.json \
  --serial "$ANDROID_SERIAL" \
  --repetitions 1 \
  --out-dir benchmarks/results/phase3-feasibility
```

Recorded output:

```text
benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json
sha256: 81e92389437bfe9b02ab7c0b9d5dd41a29f5308bfbbd4711692b4ff114e58932
```

The harness compares `--spec-type none` with the same target plus:

```text
--model-draft /data/local/tmp/llama-edge/models/mtp-gemma-4-E2B-it.gguf
--spec-type draft-mtp
--spec-draft-n-max 4
```

It checks source/binary/model fingerprints, paired greedy output, speculation activation, minimum
speedup, RSS, swap, and ZRAM gates before producing a pass/fail/inconclusive verdict.

### Recorded native-MTP feasibility verdict

The run used one repetition of each of the three fixed prompts per mode. It is sufficient to decide
whether a five-repetition promotion run is warranted, not to establish a repeatable throughput
estimate.

| Metric | Baseline | Native MTP |
|---|---:|---:|
| Successful runs | 3/3 | 3/3 |
| Mean predicted decode | 4.91956 tok/s | 4.82293 tok/s |
| TTFT mean | 3,736.37 ms | 3,348.08 ms |
| End-to-end mean | 29,349.75 ms | 29,563.29 ms |
| Peak process VmHWM | 2,027,504 kB | 1,902,124 kB |
| Minimum `MemAvailable` | 1,914,244 kB | 1,744,768 kB |
| `SwapFree` drop | 450,620 kB | 150,028 kB |

Native MTP proposed 605 draft tokens and accepted 226: 37.355% acceptance. Every paired greedy
output matched exactly. Nevertheless, decode was **0.98036×** baseline, below the predeclared
1.03× gate, and `SwapFree` dropped in both modes. ZRAM telemetry was unavailable, so the ZRAM gate
was inconclusive rather than passed. Both sessions started at thermal status 2
(37.9 °C baseline, 38.4 °C MTP), another reason this artifact is feasibility evidence rather than a
final clean-device benchmark.

Verdict: **fail**. Native MTP is rejected on this device; Phase 3 proceeds to zero-weight n-gram
speculation on the verified Llama model.

## 8. Smoke generation

Always pass a small explicit context. The model's 128K default thrashed this device; `-c 512`
restored normal operation.

```bash
adb shell "cd /data/local/tmp/llama-edge && ./llama-completion \
  -m models/Llama-3.2-1B-Instruct-Q4_0.gguf \
  -p 'The three primary colors are' \
  -n 32 --temp 0 -no-cnv -c 512 -t 6 --seed 1"
```

The Phase 0 evidence contains a coherent completion and timing at two A78-pinned threads. A fresh
run must be reviewed for coherent output; matching a command exit code is not a correctness test.

## 9. Device preparation

Before each measured session:

- Enable airplane mode manually and disable Wi-Fi.
- Close foreground applications and set brightness to minimum.
- Start between 60% and 80% battery, disconnected from charging except when USB ADB is required.
- Require thermal status `NONE`; record battery temperature and `MemAvailable`.
- Keep the screen awake during the run, then undo it when the session ends.
- Use a two-minute idle cooldown between cases unless the protocol explicitly specifies another
  interval.

```bash
export ANDROID_SERIAL=<phone-serial>
adb shell svc power stayon usb
python tools/device_snapshot.py -o /tmp/mobilespec-device.json
```

`device_snapshot.py` has no `--serial` option. Set `ANDROID_SERIAL` when more than one
device/emulator is attached, and inspect the JSON for non-empty identity fields before using it.

At the end of the session:

```bash
adb shell svc power stayon false
```

## 10. Preview and run the official suite

The dry run needs no device, but it creates a timestamped directory containing dry-run result
placeholders. Do not commit or cite that directory as measured evidence.

```bash
python tools/run_suite.py \
  benchmarks/suites/phase1-baseline.json \
  --dry-run \
  --ndk-version 28.2.13676358
```

Measured run:

```bash
export ANDROID_SERIAL=<phone-serial>
python tools/run_suite.py \
  benchmarks/suites/phase1-baseline.json \
  --serial "$ANDROID_SERIAL" \
  --device-snapshot /tmp/mobilespec-device.json \
  --ndk-version 28.2.13676358
```

The suite runs 12 cases, five repetitions per case, with warmup enabled and a 120-second cooldown.
It is unpinned unless a case explicitly supplies `cpuMask`. The official run took 48 minutes
17 seconds.

Summarize the newly printed result directory:

```bash
python tools/summarize_results.py \
  benchmarks/results/raw/<timestamp>-phase1-baseline \
  -o /tmp/mobilespec-baseline.md
```

## 11. Verify the committed evidence

This check validates structure and the documented source commit/model hash. It does not rerun the
phone benchmark.

```bash
python - <<'PY'
import json
from pathlib import Path

root = Path("benchmarks/results/raw/20260723-135809-phase1-baseline")
files = sorted(root.glob("*.json"))
assert len(files) == 12, len(files)

expected_app = "4462c70587f9cdd6d00b67b5964cac060014c7a3"
expected_llama = "178a6c4"
expected_model = "fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8"

for path in files:
    result = json.loads(path.read_text())
    assert result["schemaVersion"] == 1, path
    assert result["dryRun"] is False, path
    assert result["software"]["appCommit"] == expected_app, path
    assert result["software"]["llamaCppCommit"] == expected_llama, path
    assert result["model"]["sha256"] == expected_model, path
    assert result["rawBench"], path
print(f"verified {len(files)} committed result files")
PY
```

Expected:

```text
verified 12 committed result files
```

## 12. Result provenance

Each schema-version-1 JSON records:

- case and suite IDs;
- UTC timestamp;
- device model, Android version, SoC, serial, thermal/battery/memory start and end;
- project commit, llama.cpp commit, build variant, and NDK version;
- model ID, SHA-256, quantization, and context metadata;
- backend, thread count, GPU layers, workload size, and repetition count;
- mean, standard deviation, and every per-repetition throughput sample;
- the complete JSON emitted by `llama-bench`.

Known schema limitations:

- Vulkan device/driver are null unless imported from a populated snapshot; the current snapshot
  tool does not collect Vulkan identity.
- Upstream `llama-bench` at this pin exposes neither context nor seed for synthetic pp/tg cases.
  Patch `0002` adds `-c/--ctx-size`, emits resolved `n_ctx`, and Phase 3B rejects a record unless it
  equals the declared 512-token APK context. Seed remains metadata and correctness is checked with
  real fixed-seed generation.
- The Phase 1 `run_suite.py` harness records `MemAvailable`, not peak RSS or swap delta. The
  separate Phase 3 `run_mtp_ab.py` artifact records process VmHWM, swap, and memory samples.

## 13. Android debug build and launch smoke

Final Android pins:

| Component | Value |
|---|---|
| Gradle | 8.11.1 |
| Android Gradle Plugin | 8.9.1 |
| Kotlin | 2.3.0 |
| compile/target/min SDK | 36 / 36 / 28 |
| Android NDK | 28.2.13676358 |
| CMake | 3.22.1 |
| Java runtime | OpenJDK 21.0.11 |
| Java/Kotlin bytecode target | 17 |
| ABI | arm64-v8a |

The final central validation command was:

```bash
ANDROID_HOME=/absolute/path/to/Android/Sdk \
  ./gradlew --no-daemon test assembleDebug assembleRelease
```

For another host, replace `ANDROID_HOME` with the absolute SDK path. The run completed 181 tasks
with `BUILD SUCCESSFUL`, including:

- `:engine-api:test`;
- `:engine-llama:testDebugUnitTest`;
- `:engine-llama:testReleaseUnitTest`;
- the arm64 release JNI/CMake build;
- `:app:assembleDebug`;
- `:app:assembleRelease`.

Produced artifacts:

```text
app/build/outputs/apk/debug/app-debug.apk
size:   39,284,593 bytes
sha256: 6228b85028a8f016ea6463f420da0a47e66264b87c3e3c8944922b66fcc87385

app/build/outputs/apk/release/app-release.apk
size:   28,836,295 bytes
sha256: 345c2d81fdf1a921b3617976ca9daaf2a82f5d0b3588c4a90c79719be2aa1a0b
```

Both APKs contain `lib/arm64-v8a/libmobilespec_llama.so` and
`lib/arm64-v8a/libc++_shared.so`, and both verify with APK Signature Scheme v2. They do not carry
v1, v3, v3.1, or v4 signatures.

### Experimental KleidiAI + Vulkan/hybrid Android build

The bounded Phase 5 backend branch keeps the pinned
`-march=armv8.2-a+dotprod+fp16` CPU target and adds two independently switchable Gradle/CMake
properties. Both default on:

```bash
ANDROID_HOME=/absolute/path/to/Android/Sdk \
  ./gradlew --no-daemon test :app:assembleDebug
```

The proven CPU-only release path remains buildable without either experiment:

```bash
ANDROID_HOME=/absolute/path/to/Android/Sdk \
  ./gradlew --no-daemon \
    -Pmobilespec.enableKleidiAI=false \
    -Pmobilespec.enableVulkan=false \
    :app:assembleDebug
```

The Vulkan build uses the pinned `third_party/Vulkan-Headers`, the committed SPIR-V CMake shim,
and the NDK host `glslc`; it does not require a separately installed LunarG SDK. After building,
emit machine-readable source/toolchain/artifact identities and verify that both `kai_*` and
`ggml_backend_vk_*` symbols are present and that the JNI library links `libvulkan.so`:

```bash
ANDROID_NDK_HOME=/absolute/path/to/Android/Sdk/ndk/28.2.13676358 \
  python tools/inspect_android_build.py
```

The report also reads the KleidiAI pin from the checked-out llama.cpp CMake source, verifies the
downloaded archive against that source's MD5, records its SHA-256, and lists the license files in
the archive. For the current pin these are KleidiAI `v1.24.0`, archive SHA-256
`9348b969e042d8890a54b01a463dbe71f5a4c074b5329e9c26a85ef3b68aa19b`, and the bundled
Apache-2.0 and BSD-3-Clause texts. The downloaded archive and extracted source remain under the
ignored `.cxx/` build tree.

On 8 August 2026 this local build passed in 3m41s. This proves packaging and symbol presence only;
it is not device correctness, stability, or speed evidence. Since the native binary changed, the
previously measured Android phase policy is disabled on this branch until the final binary is
frozen and an explicitly approved re-sweep refreshes the policy and hashes.

The Models screen also contains a bounded backend-qualification action. It runs CPU and available
partial/full GPU candidates through fail-fast correctness, cancellation/reuse, device-loss,
memory/SwapFree, thermal, native-timing, stability, and counterbalanced performance gates. Timing
is skipped when required operation-shape evidence is absent or has already failed. Export the raw
qualification JSON from the screen, then validate it without changing source:

```bash
python tools/export_android_backend_policy.py /absolute/path/to/qualification.json --check
```

Only a report with one fully qualified candidate and exact profile identity can generate a bundled
Auto policy. Unknown GPUs therefore remain on CPU until device evidence is supplied. The retained
PowerVR BXM operation failures reject that known family before timing. The release CPU policy is
generated from the accepted six-gate report and its rebuilt stripped benchmark is byte-identical
(`459de42359a7abc37ac1e8b0df0ef20b54175d6e09b1d65dbeedd873846fc68b`) to the report identity.
No GPU policy was promoted on the tested PowerVR device; Auto therefore retains CPU.

Final builds embed the committed app identity plus source, pinned llama.cpp, pinned patch-series,
native-library, phase-policy, device, model, and backend identities. The release bundle manifest
and its `SHA256SUMS` are the canonical record for the published APK, avoiding stale hashes in this
document whenever a provenance-only commit changes the APK bytes.

```bash
TASK_APKSIGNER=/absolute/path/to/Android/Sdk/build-tools/36.0.0/apksigner
"$TASK_APKSIGNER" verify --verbose app/build/outputs/apk/debug/app-debug.apk
"$TASK_APKSIGNER" verify --verbose app/build/outputs/apk/release/app-release.apk
```

The final phase-aware debug APK was installed and passed model import/hash/load, generation,
baseline/optimized A/B export, cancellation, and post-cancel reuse. The following earlier smoke
records the original cold-launch timing:

```bash
export ANDROID_SERIAL=<phone-serial>
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -W -n com.manishm.mobilespec/.MainActivity
adb shell pidof com.manishm.mobilespec
```

`am start -W` returned `Status: ok` with a 2,442 ms cold launch, and `pidof` returned a live
process. This timing covers app startup, `System.loadLibrary`, and native capability initialization
from the ViewModel; the later final-build evidence covers the additional debug flows.

The release build deliberately uses the debug signing configuration for a reproducible demo APK.
It installed and cold-launched successfully on the phone:

```bash
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell am start -W -n com.manishm.mobilespec/.MainActivity
adb shell pidof com.manishm.mobilespec
```

`am start -W` returned `Status: ok` in 1,125 ms and the process remained alive. This verifies
startup, not model generation. The debug-signed APK is not suitable for store distribution without
a private release key.

## 14. Final phase-aware evidence reproduction

The completed three-round discovery report is:

```text
benchmarks/results/raw/20260807-232905-autotune/autotune.json
sha256: 7afd9ae000d57bf1bce0e3d38de13d99ffd779a34bbb591f9e41dabffc56a7ab
winner: prefill 8 / decode 2; stock: prefill 8 / decode 8; all six gates passed
```

The Android confirmation plus three sustained suites can be revalidated and charted without a
phone:

```bash
python tools/summarize_real_generation.py \
  --confirmation benchmarks/results/20260807-real-generation/mobilespec-1786126555507.json \
  --sustained benchmarks/results/20260807-real-generation/mobilespec-1786128376471.json \
              benchmarks/results/20260807-real-generation/mobilespec-1786128714979.json \
              benchmarks/results/20260807-real-generation/mobilespec-1786129049935.json \
  --output benchmarks/results/20260807-real-generation/summary.json \
  --chart docs/assets/mobilespec-phase-policy.png
```

The summary JSON SHA-256 is
`b2f447f615d51fa4c87084c17b83de78a271685d3ab21f7fe009d40f379c269f`.
It validates five native-timing runs/mode in every source file, exact output-hash correctness, and
one shared model/build/policy/device/config identity before aggregating. The three sustained suites
span 14.37 minutes and contain 15 samples/mode.

The phase-aware debug app passed SAF model import/hash/load, streamed generation, baseline and
optimized benchmark execution/export, cancellation, and post-cancel reuse on the target Redmi.
The accepted supplemental export is
`benchmarks/results/20260808-final-telemetry/mobilespec-1786188091323.json`, SHA-256
`c335c71faed284c33202463582216b94b17ce9f5342a14d410fae76635a7993f`. It records five
native-timing runs/mode, exact output hashes, complete worktree/JNI provenance, 1,760,477,184-byte
VmHWM, and SwapFree at every boundary. The existing sustained run predates those fields and is not
misrepresented as peak-RSS/swap evidence.

The final GPU-capable native library was also confirmed in
`benchmarks/results/20260810-final-release/mobilespec-1786369326067.json`, SHA-256
`edccdeeb0963e566f962a4786ed9166d4c1295fd5d8664bb077b7e36cb66c40d`. It contains five scored
runs per mode, exact output-hash equality, native timings, `pp8-tg2` identity, and Auto resolving to
CPU because no PowerVR GPU policy qualified. Every boundary reported thermal status `MODERATE`, so
this is final integration/fallback evidence; the cooler 15-run-per-mode bundle remains the
headline performance source.

## 15. Prepare (but do not publish) the release bundle

After all device gates pass and the final source is committed, build the release APK and create a
non-overwriting bundle containing its provenance, signature verification, checksums, project and
third-party notices, and the matching NDK LLVM notice:

```bash
TASK_SDK=/absolute/path/to/Android/Sdk
ANDROID_HOME="$TASK_SDK" ./gradlew --no-daemon test :app:assembleRelease
python tools/prepare_release_bundle.py \
  --apk app/build/outputs/apk/release/app-release.apk \
  --ndk "$TASK_SDK/ndk/28.2.13676358" \
  --apksigner "$TASK_SDK/build-tools/36.0.0/apksigner" \
  --version v1.0.0-arm-challenge \
  --output dist/v1.0.0-arm-challenge
```

The command refuses an existing destination, an uncommitted project tree, an APK that does not
embed the current Git commit, a missing/mismatched APK inspection hash, an invalid APK signature,
a missing patch-series check, or an ambiguous LLVM notice. The expected repository-managed
llama.cpp patch worktree is allowed and is recorded by commit plus source-diff hash.
`packagingEligible` covers package/source hygiene only—it does not mean physical qualification or
Devpost submission gates passed. `--allow-dirty` exists solely for development previews and marks
that field false.

Publish only after the final device gate passes and the bundle is produced from the committed
default branch. A release bundle records packaging provenance; it does not turn a rejected GPU
candidate into a performance claim.

## 16. Fresh-clone acceptance test

A final fresh clone passes only when all of the following are evidenced:

- [x] unauthenticated shallow clone and recursive submodule checkout succeed (10 Aug, public
      `main` at `358d458`; Vulkan-Headers required the documented unshallow fallback);
- [x] both submodule hashes match the documented pins;
- [ ] model download produces the documented size and SHA-256;
- [x] debug and release Android APKs, including CPU and Vulkan JNI backends, build from the public
      clone and all Gradle tests pass;
- [ ] smoke generation is coherent at `-c 512`;
- [ ] the standard suite produces 12 non-dry-run JSON files;
- [ ] the summarizer renders all 12 cases;
- [x] the Android release build succeeds from the documented command;
- [ ] the app can load the verified model, generate, cancel, and export a benchmark result;
- [x] native-MTP feasibility A/B runs as documented and records a failed verdict;
- [x] zero-weight n-gram fallback A/B runs as documented and is retained as rejection evidence;
- [x] stock and optimized modes differ only in the frozen phase-aware CPU policy;
- [x] performance/correctness claim inputs trace to retained immutable artifacts;
- [x] supplemental VmHWM/SwapFree and new source/JNI provenance export is retained.

On 8 Aug, an authenticated shallow recursive clone of commit `c854112163e1` checked out llama.cpp
`178a6c449371` and Vulkan-Headers `8864cdc896bb`, then passed `test assembleRelease` in 54 seconds
(146 tasks: 118 executed, 28 from cache). Public logged-out cloning, native CPU/Vulkan rebuilding,
model acquisition, and the complete device-suite sequence remain open and are not inferred from
the Android build.
