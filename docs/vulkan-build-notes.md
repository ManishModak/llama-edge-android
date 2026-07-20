# Vulkan-on-Android Cross-Compile Notes (llama.cpp)

Target: llama.cpp Vulkan backend (`GGML_VULKAN=ON`) cross-compiled for Android
arm64-v8a (android-28), for a PowerVR BXM-8-256 (Vulkan 1.3) device.

- llama.cpp pinned commit: `178a6c4` (upstream `b10069`), submodule at
  `third_party/llama.cpp`.
- Host: Windows 11.
- NDK: `C:\Users\win-home\AppData\Local\Android\Sdk\ndk\28.2.13676358`
- CMake/Ninja: `C:\Users\win-home\AppData\Local\Android\Sdk\cmake\3.22.1\bin`

## STATUS: BLOCKED (host prerequisites missing)

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
