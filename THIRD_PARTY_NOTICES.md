# Third-Party Notices

MobileSpec is licensed under Apache-2.0. The source tree and Android application also use the
following third-party components. Their licenses remain independent of the MobileSpec license.
Model files are not distributed by this repository or its APK.

## Native runtime

| Component | Pinned identity | Use | License evidence |
|---|---|---|---|
| llama.cpp and ggml | `178a6c44937154dc4c4eff0d166f4a044c4fceba` | Statically linked inference runtime | MIT; `third_party/llama.cpp/LICENSE` |
| Arm KleidiAI | `v1.24.0`; archive SHA-256 `9348b969e042d8890a54b01a463dbe71f5a4c074b5329e9c26a85ef3b68aa19b` | Optional statically compiled Arm CPU kernels | Apache-2.0; fetched archive `LICENSES/Apache-2.0.txt` |
| Vulkan-Headers | `8864cdc896bbc2a9b6eb36b3218fc9ef57908d77` (`v1.4.350`) | Vulkan API headers used by the optional GPU backend | Apache-2.0 OR MIT; `third_party/Vulkan-Headers/LICENSE.md` |
| LLVM libc++ shared runtime | Android NDK `28.2.13676358` | Packaged as `libc++_shared.so` | Apache-2.0 WITH LLVM-exception; NDK `toolchains/llvm/prebuilt/<host>/NOTICE` |

The KleidiAI source archive also carries a BSD-3-Clause text for its vendored GoogleTest metadata.
MobileSpec configures `KLEIDIAI_BUILD_TESTS=OFF`; that test dependency is not part of the Android
runtime. `tools/inspect_android_build.py` records the fetched archive hashes and license inventory.

The llama.cpp MIT notice is reproduced here for binary-release visibility:

> MIT License
>
> Copyright (c) 2023-2026 The ggml authors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
> BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Kotlin and Android runtime libraries

The app uses AndroidX Core, Activity, Lifecycle, Jetpack Compose UI/Material 3, and
`kotlinx-coroutines-android`. Their published Maven metadata declares Apache-2.0. JUnit is used only
for host-side tests and is not packaged in the APK; its Maven metadata declares EPL-1.0.

## Release packaging requirement

Every published APK release bundle must include:

1. the root `LICENSE`;
2. this `THIRD_PARTY_NOTICES.md`;
3. the NDK LLVM `NOTICE` matching the NDK that supplied `libc++_shared.so`;
4. the APK and a checksum manifest identifying the exact project, llama.cpp, Vulkan-Headers, and
   native-library revisions.

This file is an attribution inventory, not legal advice. The upstream license files are
authoritative.
