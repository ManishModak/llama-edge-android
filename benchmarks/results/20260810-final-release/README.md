# Final GPU-capable release confirmation

`mobilespec-1786369326067.json` is the app-exported, counterbalanced five-run-per-mode confirmation
from the final GPU-capable Android native library on the Redmi Note 14 5G.

- Artifact SHA-256: `edccdeeb0963e566f962a4786ed9166d4c1295fd5d8664bb077b7e36cb66c40d`
- Native library SHA-256: `c486ac32445ace1158d213c1d9c94ca09a0be951d993d8b826cf0f31deb70ee4`
- Model SHA-256: `fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8`
- Active backend: CPU; Auto rejected the unqualified PowerVR GPU/hybrid paths
- Baseline policy: prefill 8 / decode 8
- Optimized policy: prefill 8 / decode 2
- Correctness: all ten scored runs produced the same output SHA-256
- Baseline mean: 1.33854 tok/s; optimized mean: 5.49628 tok/s (4.1062x)

This was a hot final integration confirmation, not the headline performance run: every scored
boundary reported Android thermal status `MODERATE`, and the display timed out during part of the
counterbalanced session before being forced awake. The artifact is retained to prove the final
native-library/policy/backend integration and exact-output correctness. The cooler, controlled
15-run-per-mode bundle remains the source for the 2.0739x headline claim.
