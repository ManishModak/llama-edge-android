# Vulkan-on-Android Cross-Compile Notes (llama.cpp)

Target: llama.cpp Vulkan backend (`GGML_VULKAN=ON`) cross-compiled for Android
arm64-v8a (android-28), for a PowerVR BXM-8-256 (Vulkan 1.3) device.

- llama.cpp pinned commit: `178a6c4` (upstream `b10069`), submodule at
  `third_party/llama.cpp`.
## STATUS (23 Jul 2026): ✅ RESOLVED — builds and runs on Linux

The Windows Smart App Control blocker is gone: on Linux the host `vulkan-shaders-gen`
compiles **and executes** with the system `g++`, exactly as predicted. Two *new*
blockers surfaced that the Windows side never reached, both header-resolution
problems in the cross-compile. Both are solved below.

Host: CachyOS (Arch), GCC 16.1.1, CMake 4.3.4, ninja 1.13.1.
NDK: `~/Android/Sdk/ndk/28.2.13676358` (r28c).

### Linux blocker A — `vulkan/vulkan.hpp` not found

The NDK sysroot ships the Vulkan **C** headers (`vulkan.h`, `vulkan_core.h`) and
`libvulkan.so`, but **not** the C++ bindings header `vulkan.hpp`, which
`ggml-vulkan.cpp` includes. `vulkan.hpp` normally arrives with the LunarG SDK or a
distro `vulkan-headers` package — neither applies to an Android cross-build.

Fix: vendor Khronos **Vulkan-Headers pinned at `v1.4.350`** (commit `8864cdc`) as a
submodule at `third_party/Vulkan-Headers`, and point CMake at it:

```
-DVulkan_INCLUDE_DIR=$PWD/third_party/Vulkan-Headers/include
```

`v1.4.350` was chosen to match the system glslang/SPIRV-Tools already present
(1.4.350.1). These headers are used with the NDK's `libvulkan.so`; ggml resolves
post-1.1 entry points through `vk::DispatchLoaderDynamic`, so the newer headers
link cleanly against the API-28 stub library.

### Linux blocker B — `spirv/unified1/spirv.hpp` not found

`ggml/src/ggml-vulkan/CMakeLists.txt:14` calls
`find_package(SPIRV-Headers CONFIG REQUIRED)` but **never links
`SPIRV-Headers::SPIRV-Headers` to the `ggml-vulkan` target** (line 112 links only
`Vulkan::Vulkan`). Upstream gets away with this because a system-installed
spirv-headers package puts `spirv/unified1/spirv.hpp` on the default include path.
In a cross-compile against the NDK sysroot there is no such path, so the include
fails even though the shim resolved the package.

Fix without patching the pinned submodule — inject the NDK's own copy via flags:

```
-DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16 -isystem \
  $NDK/sources/third_party/shaderc/third_party/spirv-tools/external/spirv-headers/include"
```

(`-isystem` rather than `-I` so the vendored headers don't trip the build's
`-Wall -Wextra -Wpedantic`.) The committed `tools/cmake/SPIRV-Headers` shim is
still required to satisfy `find_package`; keep both.

### Working Linux build (reproducible)

```bash
export NDK=$HOME/Android/Sdk/ndk/28.2.13676358
SPV="$NDK/sources/third_party/shaderc/third_party/spirv-tools/external/spirv-headers/include"

cmake -G Ninja -B build-android-vulkan -S third_party/llama.cpp \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DGGML_OPENMP=OFF -DGGML_LLAMAFILE=OFF -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod+fp16" \
  -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod+fp16 -isystem $SPV" \
  -DGGML_VULKAN=ON \
  -DVulkan_GLSLC_EXECUTABLE=$NDK/shader-tools/linux-x86_64/glslc \
  -DVulkan_INCLUDE_DIR=$PWD/third_party/Vulkan-Headers/include \
  -DSPIRV-Headers_DIR=$PWD/tools/cmake/SPIRV-Headers

cmake --build build-android-vulkan --target llama-bench llama-completion -j12
```

Deploy stripped (the unstripped binaries are ~154 MB; keep them locally for Phase 2
symbolization):

```bash
STRIP=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip
$STRIP -o dist/vulkan/llama-bench-vulkan build-android-vulkan/bin/llama-bench
adb push dist/vulkan/llama-bench-vulkan /data/local/tmp/llama-edge/
```

### Device capability readout (first successful run)

```
ggml_vulkan: 0 = PowerVR B-Series BXM-8-256 (PowerVR B-Series Vulkan Driver)
  uma: 1 | fp16: 1 | bf16: 0 | fp4: 0 | warp size: 128
  shared memory: 16384 | int dot: 0 | matrix cores: none
```

Load-bearing for Gate G1: **`int dot: 0`** (no `VK_KHR_shader_integer_dot_product`,
so Q4_0 matmul gets no integer-dot acceleration), **`matrix cores: none`**, and only
**16 KB shared memory** (vs 32–64 KB typical), which caps `mul_mm` tile sizes.
`uma: 1` is the one favourable property — no host↔device copy cost.

### Caveat: the NDK's glslc is old

`$NDK/shader-tools/linux-x86_64/glslc` is **shaderc v2022.3 (ndk-r27-beta1)**; the
system `/usr/bin/glslc` is **2026.2**. With the NDK glslc, configure reports these
as unsupported and compiles no shader variants for them:
`GL_KHR_cooperative_matrix`, `GL_NV_cooperative_matrix2`,
`GL_EXT_integer_dot_product`, `GL_EXT_bfloat16`, `GL_EXT_float_e2m1/e4m3`.

The device reports `int dot: 0` and `matrix cores: none`, so most of these would be
inert anyway — but rebuilding with the system glslc is a cheap, well-scoped
experiment for Phase 2 / workstream B. Not done yet.

---

## Historical: Windows investigation (superseded)

Everything below documents the Windows-host attempt and why it was abandoned.
Retained as evidence; the Linux recipe above is the one to use.

- Host: Windows 11.
- NDK: `C:\Users\win-home\AppData\Local\Android\Sdk\ndk\28.2.13676358`
- CMake/Ninja: `C:\Users\win-home\AppData\Local\Android\Sdk\cmake\3.22.1\bin`

## STATUS (20 Jul, update 2): host toolchain SOLVED — now blocked by Smart App Control

Progress since the first investigation:

- **Blocker 1 (SPIRV-Headers) — SOLVED.** `tools/cmake/SPIRV-Headers/SPIRV-HeadersConfig.cmake`
  is a committed shim exposing the NDK's bundled SPIR-V headers as the imported
  target. Pass `-DSPIRV-Headers_DIR=<repo>/tools/cmake/SPIRV-Headers`. No LunarG
  SDK needed.
- **Blocker 2 (no host C++ compiler) — SOLVED.** Portable **w64devkit 2.8.0**
  (GCC 16.1.0) extracted to `C:\Tools\w64devkit` (not installed system-wide, not
  in the repo). With `C:\Tools\w64devkit\bin` prepended to PATH,
  `detect_host_compiler()` picks real host GCC and **`vulkan-shaders-gen.exe`
  compiles and links successfully**.
- **Blocker 3 (NEW) — Windows Smart App Control blocks the generated tool.**
  Building reaches the shader-generation step and every invocation dies with:

  ```
  'build-android-vulkan\Release\vulkan-shaders-gen.exe' was blocked by your
  organization's Device Guard policy.
  ```

  Confirmed cause: `Get-MpComputerStatus` → `SmartAppControlState: On`, and
  `Win32_DeviceGuard` → `UsermodeCodeIntegrityPolicyEnforcementStatus: 2`
  (enforced). Smart App Control is reputation-based: w64devkit's own signed/known
  binaries run fine, but a **freshly compiled, unsigned local .exe has no
  reputation and is blocked**. This affects any locally built host tool, not just
  llama.cpp.

### Options to get past Blocker 3

| Option | Cost | Notes |
|---|---|---|
| **Build inside WSL2 (Ubuntu)** | ~2–3 GB distro install | Recommended. SAC does not police ELF binaries in WSL. Use the Linux NDK inside WSL; adb/push can stay on Windows. WSL2 is already enabled on this machine (only a `docker-desktop` distro exists today). |
| **Build on the Linux side of the dual boot** | zero install | Cleanest, but requires rebooting to rebuild the Vulkan variant. Needs the NDK set up under Linux. |
| **Build natively on the phone (Termux)** | slow | arm64-native, dodges Windows entirely; poor iteration speed. |
| **Turn Smart App Control off** | ⚠ irreversible | Windows Security → App & browser control → Smart App Control → Off. **Cannot be re-enabled without reinstalling Windows.** User decision only; not performed as part of this project. |

The CPU-only build is unaffected because it never has to execute a freshly built
host binary.

## Original investigation: BLOCKED (host prerequisites missing)

The Android/arm64 target side is fully satisfied by the NDK, and CMake
**configure succeeds**. The build is blocked at the point where it must build a
**host** helper tool (`vulkan-shaders-gen.exe`), because this machine has **no
host C++ toolchain** capable of producing a Windows executable. A secondary
issue (`SPIRV-Headers` CMake package) is also present but is easy to satisfy.

No binaries were produced. `llama-cli` / `llama-bench` were NOT built.

## Why the Vulkan backend needs a host build step

Unlike the CPU-only build, the Vulkan backend generates its SPIR-V shader code
at build time. `ggml/src/ggml-vulkan/CMakeLists.txt` (at the pinned commit):

1. `find_package(Vulkan COMPONENTS glslc REQUIRED)` - needs Vulkan headers, a
   Vulkan library, and `glslc`.
2. `find_package(SPIRV-Headers CONFIG REQUIRED)` - needs an installed
   `SPIRV-HeadersConfig.cmake` package.
3. Builds a **host** executable `vulkan-shaders-gen` via `ExternalProject_Add`.
   When cross-compiling it configures a separate host sub-build using a
   host C++ compiler detected by `detect_host_compiler()` (searches PATH for
   `cl` / `gcc` / `clang`). This tool runs on the host during the main build to
   emit `ggml-vulkan-shaders.hpp` and per-shader `.cpp` files, invoking `glslc`.

So a working **host** C++ compiler (producing Windows .exe) plus `glslc` and
`SPIRV-Headers` are mandatory prerequisites, independent of the NDK.

## What the NDK already provides (target side - OK)

- `glslc.exe`: `…\ndk\28.2.13676358\shader-tools\windows-x86_64\glslc.exe`
- Android Vulkan loader: `…\sysroot\usr\lib\aarch64-linux-android\28\libvulkan.so`
- Vulkan headers: `…\sysroot\usr\include\vulkan\vulkan_core.h`
- SPIR-V headers (source only, no CMake config):
  `…\ndk\28.2.13676358\sources\third_party\shaderc\third_party\spirv-tools\external\spirv-headers\include`
  (contains `spirv/unified1/spirv.hpp`)

## Blocker 1 (easy): `find_package(SPIRV-Headers CONFIG REQUIRED)` fails

Configure error:

```
CMake Error at ggml/src/ggml-vulkan/CMakeLists.txt:14 (find_package):
  Could not find a package configuration file provided by "SPIRV-Headers"
  with any of the following names:
    SPIRV-HeadersConfig.cmake
    spirv-headers-config.cmake
```

The NDK ships the SPIR-V header *sources* but not an installed CMake package
config, so `find_package(... CONFIG)` cannot locate them.

Workaround used here to get past configure (does NOT modify the submodule):
a synthesized `SPIRV-HeadersConfig.cmake` was written to the scratchpad
pointing `INTERFACE_INCLUDE_DIRECTORIES` at the NDK's bundled
`spirv-headers/include`, then passed via `-DSPIRV-Headers_DIR=<that dir>`.
The proper fix is to install the LunarG Vulkan SDK, whose `Include` directory
provides SPIRV-Headers (and a config), or a `spirv-headers` dev package.

## Blocker 2 (fundamental): no host C++ toolchain to build vulkan-shaders-gen

There is no host compiler on this machine:
- No Visual Studio / Build Tools (`vswhere.exe` absent; no
  `C:\Program Files\Microsoft Visual Studio` / `(x86)` install).
- No `cl.exe`, `gcc`, `g++`, MinGW, MSYS2, or standalone LLVM on PATH.

`detect_host_compiler()` nonetheless finds the NDK's own
`toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe` / `clang++.exe`
(discoverable via the compiler dir) and configure proceeds, selecting it as the
"host compiler". This is a false positive: the NDK clang defaults to
`Target: x86_64-w64-windows-gnu` (MinGW ABI) and the NDK bundles **no** host
Windows/MinGW libraries. Building the host tool fails at link:

```
lld: error: unable to find library -lkernel32
lld: error: unable to find library -luser32
...
lld: error: unable to find library -lmingw32
lld: error: unable to find library -lgcc
lld: error: unable to find library -lmsvcrt
clang: error: linker command failed with exit code 1
```

The NDK clang can compile host object files but cannot link a host Windows
executable because it has only the Android sysroot, not a Windows CRT/import
libs. This is the last-resort "NDK host clang" path - it does not work, as
anticipated.

(Note: a separate, minor issue is that the `vulkan-shaders-gen` ExternalProject
sub-build inherits `-GNinja` but not `CMAKE_MAKE_PROGRAM`, so `ninja` must be on
PATH for that step. This was handled by prepending the SDK cmake `bin` dir to
PATH and is not itself a blocker.)

## What worked up to the blocker (reproducible)

The following configure succeeds (run from `C:\Projects\llama-edge-android`,
Git Bash). It only fails when the build reaches the host `vulkan-shaders-gen`
step (Blocker 2).

```bash
CMAKE="/c/Users/win-home/AppData/Local/Android/Sdk/cmake/3.22.1/bin/cmake.exe"
NINJA="/c/Users/win-home/AppData/Local/Android/Sdk/cmake/3.22.1/bin/ninja.exe"
NDK="/c/Users/win-home/AppData/Local/Android/Sdk/ndk/28.2.13676358"
GLSLC="$NDK/shader-tools/windows-x86_64/glslc.exe"

# ninja must be on PATH for the vulkan-shaders-gen ExternalProject sub-build:
export PATH="/c/Users/win-home/AppData/Local/Android/Sdk/cmake/3.22.1/bin:$PATH"

# SPIRV-Headers config workaround (see Blocker 1); replace with LunarG SDK path
# once installed:
SPVDIR=<dir containing a SPIRV-HeadersConfig.cmake>

"$CMAKE" -G Ninja -B build-android-vulkan -S third_party/llama.cpp \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-28 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON \
  -DGGML_OPENMP=OFF \
  -DGGML_LLAMAFILE=OFF \
  -DLLAMA_CURL=OFF \
  -DVulkan_GLSLC_EXECUTABLE="$GLSLC" \
  -DSPIRV-Headers_DIR="$SPVDIR"

# Then (currently FAILS at host vulkan-shaders-gen link step):
# "$CMAKE" --build build-android-vulkan --target llama-cli llama-bench -j
```

## What the user must install to unblock

To complete this Vulkan Android cross-build, install on the Windows host **one**
host C++ toolchain plus SPIRV-Headers/glslc:

1. A host C++ compiler that produces Windows executables. Easiest options:
   - **Visual Studio 2022 Build Tools** with "Desktop development with C++"
     (provides MSVC `cl.exe`), OR
   - **MinGW-w64** via `w64devkit` or MSYS2 (`mingw-w64-ucrt-x86_64-gcc`).
   `detect_host_compiler()` will then pick a real host compiler, and
   `vulkan-shaders-gen.exe` will link.

2. The **LunarG Vulkan SDK** for Windows
   (https://vulkan.lunarg.com/sdk/home#windows). Its `Include` directory
   supplies SPIRV-Headers (resolving Blocker 1) and it ships `glslc`. After
   installing, `VULKAN_SDK` is set and `find_package(SPIRV-Headers CONFIG)`
   succeeds without the synthesized-config workaround.
   (The MSYS2 route can instead use `mingw-w64-ucrt-x86_64-vulkan-devel`,
   `-shaderc`, `-spirv-headers`.)

With those installed, re-run the configure command above (dropping the
`SPIRV-Headers_DIR` override if the SDK is on `CMAKE_PREFIX_PATH`), then build
`--target llama-cli llama-bench`. The Android/arm64 target side needs nothing
further from the NDK - `glslc`, `libvulkan.so`, and the Vulkan headers are
already present.

## Verification (blocked)

Not reached. Once built, verify arch with the NDK llvm tools, e.g.:
`…\ndk\28.2.13676358\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-readelf.exe -h build-android-vulkan\bin\llama-cli`
(expect `Machine: AArch64`).
