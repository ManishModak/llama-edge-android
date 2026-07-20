# llama-edge-android (MobileSpec)

Adaptive quantized LLM decoding for Arm Android — llama.cpp benchmark harness, optimization, and demo app for the **Arm Create: AI Optimization Challenge 2026** (Mobile AI track).

> **Status: Phase 0 — scaffolding.** Headline results, demo video, and reproduction steps land here as they are produced. See [docs/PLAN.md](docs/PLAN.md).

## Target device

Redmi Note 14 5G — MediaTek Dimensity 7025 (2× Cortex-A78 + 6× Cortex-A55, dotprod/fp16), PowerVR BXM-8-256 (Vulkan 1.3), 8 GB RAM, Android 16.

## Repository layout

| Path | Purpose |
|---|---|
| `app/` | Kotlin/Compose demo + benchmark UI (Phase 4) |
| `engine-api/` | Runtime-neutral Kotlin engine contracts |
| `engine-llama/` | JNI + C++ integration with llama.cpp |
| `third_party/llama.cpp/` | Pinned git submodule |
| `benchmarks/` | Suites, fixed prompts, result summaries |
| `models/manifest.json` | Model sources + sha256 (no weights in git) |
| `tools/` | Cross-platform (Python) build/bench/ADB scripts |
| `docs/` | Plan, methodology, reproducibility, optimization notes |

## Quick start

```
git clone --recurse-submodules https://github.com/ManishModak/llama-edge-android.git
python tools/device_snapshot.py          # verify connected device
# build + bench instructions: docs/reproducibility.md (in progress)
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
