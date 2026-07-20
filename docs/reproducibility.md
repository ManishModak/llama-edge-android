# Reproducibility

Every benchmark claim must be traceable to the exact toolchain, commits, model, and device state below.

## Pinned toolchain

| Component | Version |
|---|---|
| JDK | Android Studio JBR (record exact on first Gradle build) |
| Android NDK | 28.2.13676358 |
| CMake | 3.22.1 (Android SDK) |
| Gradle / AGP / Kotlin | TODO (Phase 4) |
| minSdk / targetSdk | TODO |
| llama.cpp submodule commit | see `git submodule status` — recorded per result bundle |

## Host machines

- Windows 11 Pro (primary) — dual boot with Linux; both configured with Android SDK/NDK.
- Model weights live on the shared exFAT drive, outside the repo. Set env var `LLAMA_EDGE_MODELS` to that directory (e.g. `D:\models` / `/mnt/data/models`).

## Build commands

TODO Phase 0/1: NDK cross-compile of llama-cli/llama-bench (CPU and Vulkan), adb push + run.

## Device preparation checklist (before any measured run)

- [ ] Airplane mode ON, Wi-Fi off, screen brightness minimum
- [ ] Battery 60–80 %, not charging
- [ ] Thermal status NONE at start (`python tools/device_snapshot.py`)
- [ ] No foreground apps; screen kept on via `adb shell svc power stayon usb`
- [ ] 2 min cooldown between repetitions
