# KleidiAI go/no-go — Redmi Note 14 5G

**Decision: reject for the release binary.** The 8 Aug 2026 bounded same-device spike activated
KleidiAI and preserved deterministic output, but it did not satisfy the performance gate. The
release therefore keeps KleidiAI off and retains the previously verified CPU policy.

## Matched build and activation evidence

- Device: Redmi Note 14 5G (`24094RAD4I`), MediaTek MT6855 / Dimensity 7025.
- Model: Llama 3.2 1B Instruct Q4_0,
  SHA-256 `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`.
- llama.cpp: `178a6c4`; Android NDK `28.2.13676358`.
- Both builds: `armv8.2-a+dotprod+fp16`, `GGML_NATIVE=OFF`, `GGML_OPENMP=OFF`,
  `GGML_LLAMAFILE=OFF`; the intended build delta was only `GGML_CPU_KLEIDIAI`.
- Control `llama-bench` SHA-256:
  `cad23be44f02912ef2e329a783519b9aadeb981b2eb80453f7bfd3e9fd54af56`.
- KleidiAI `llama-bench` SHA-256:
  `8cc4b692e9bef3288c18506b744c5cef15a5a66795ae3a072e80c16f2a223403`.
- CMake reported `Using KleidiAI optimized kernels if applicable`; the candidate contained 58
  `kai_*` symbols. On device, llama.cpp reported
  `DOTPROD = 1 | KLEIDIAI = 1 | REPACK = 1` while the Q4_0 model was active.

## Correctness and bounded A/B

Greedy completion used the same prompt, seed, temperature zero, CPU phase pair `pp8-tg2`, context
256, and eight generated tokens. Both binaries returned the same bytes:

```text
 Paris. The capital of Germany is Berlin
```

The scored gate used pp256 and tg64, context 512, batch/ubatch 64, `n_gpu_layers=0`, one discarded
internal warm-up per process, and three external samples per mode in counterbalanced order. Each
sample started with the live skin sensor below 40 C and status 0; the device's aggregate thermal
status retained an older moderate reading, so both readings were recorded and the live HAL sensor
was the run gate.

| Metric | Control samples (tok/s) | Control mean | KleidiAI samples (tok/s) | KleidiAI mean | Delta |
|---|---:|---:|---:|---:|---:|
| pp256 | 80.194, 79.938, 81.117 | 80.416 | 78.722, 71.879, 76.402 | 75.668 | -5.90% |
| tg64 | 12.947, 13.054, 13.089 | 13.030 | 12.939, 12.842, 12.873 | 12.885 | -1.11% |

The smoke sample showed no crash or unsupported instruction and improved model load from 3,132 ms
to 2,265 ms, but inference time was effectively tied. That one load observation does not override
the scored phase regression, and TTFT was not separately instrumented because the primary
throughput gate had already failed.

## Outcome

KleidiAI is not shipped by default, the existing verified phase policy is not re-swept, and no
KleidiAI speed claim is made. The experiment remains opt-in with
`-Pmobilespec.enableKleidiAI=true` for future devices or changed model/kernel shapes.
