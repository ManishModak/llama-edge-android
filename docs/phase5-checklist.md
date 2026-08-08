# Phase 5 evidence and submission checklist

This is the final-run control sheet. Check a box only when the named immutable artifact exists and
has been inspected. A green build or successful process exit does not substitute for missing
performance, correctness, memory, or demo evidence.

## Current evidence ledger

| Requirement | Current evidence | State |
|---|---|---|
| CPU Android bring-up | `docs/phase0-report.md`, commit `0fcd0ea` | Proven |
| Official CPU/Vulkan baseline | 12 JSON files under `benchmarks/results/raw/20260723-135809-phase1-baseline/`, commit `8b464b7` | Proven |
| Decode bottleneck diagnosis | `docs/bottleneck-note.md` and `benchmarks/results/profiles/20260723-105825/`, commit `54aabbd` | Proven |
| Speculative workstream | Native MTP and n-gram both measured and rejected | Closed; rejection evidence only |
| Compact Gemma target identity/hash | Exact manifest metadata and on-device size/SHA-256 preflight pass | Proven |
| Matching MTP-head identity/hash | Exact metadata, compatibility, and on-device size/SHA-256 preflight pass | Proven |
| MTP load/memory gate | Both modes load; swap is used and ZRAM telemetry is unavailable | Failed |
| Same-target MTP-off/on A/B | 4.91956 baseline vs 4.82293 MTP, 0.98036× | Failed |
| Active optimization | Three-round sweep selected `pp8-tg2`; all six gates passed; exact report/binary/model/device identity bundled | Proven for this exact target identity |
| Exploratory phase split | Earlier two-round observation superseded by `20260807-232905-autotune/autotune.json` | Superseded; not used |
| Fixed prompt suite | Three hashed prompts measured once per mode | Proven for feasibility only |
| Native-MTP correctness | All six runs succeed and all three greedy output pairs match | Passed |
| Sustained baseline/optimized run | Three no-cooldown suites, 14.37 min, 15 runs/mode, exact hashes; separate final VmHWM/SwapFree supplement | Proven with scoped supplement |
| Android debug build/launch | SAF import/hash/load, generation, A/B, export, cancellation/reuse verified on Redmi | Proven |
| Android release/model generation | Final debug/release build and v2 signing pass; debug full flow proven; final release full flow not repeated | Partially proven |
| Fresh-clone build record | Authenticated shallow recursive clone and Android `test assembleRelease` pass; native/model/device sequence pending | Partial |
| Headline chart | `docs/assets/mobilespec-phase-policy.png`, generated from checked-in JSON | Proven |
| Screenshots/demo video | Not recorded | Blocked |
| Public repository/release | Repository documented as private | Blocked |
| Devpost submission | Not verified | Blocked |

## Freeze the final candidate

- [x] Gate G2 verdict is written: **YES for phase-aware CPU policy; speculative paths rejected**.
- [ ] Project commit is recorded:
  `________________________________________`.
- [ ] llama.cpp commit is recorded:
  `________________________________________`.
- [ ] Vulkan-Headers commit is recorded:
  `________________________________________`.
- [ ] Baseline and optimized binaries come from the same toolchain and source tree.
- [ ] A diff or configuration artifact proves the two modes differ only in the selected policy.
- [ ] No further performance changes land after the candidate is frozen.

Commands:

```bash
git status --short
git rev-parse HEAD
git submodule status
git diff --stat <baseline-ref>..<optimized-ref>
```

If the policy is a runtime switch in one binary, preserve the complete mode configurations instead
of inventing separate build refs.

## Freeze model and prompt inputs

The fields below belong to the final phase-aware CPU-policy candidate. Native MTP and n-gram inputs
are frozen historical rejection evidence and must not be substituted into the final performance
claim.

### Target model

- [ ] File name:
  `________________________________________`
- [ ] Architecture/parameter count:
  `________________________________________`
- [ ] Quantization:
  `________________________________________`
- [ ] Exact byte size:
  `________________________________________`
- [ ] Source URL and source revision:
  `________________________________________`
- [ ] SHA-256:
  `________________________________________`
- [ ] License/terms reviewed and linked.

### Draft/MTP inputs

No draft model or MTP head belongs to the final phase-aware CPU-policy candidate. Their identities
remain frozen in the historical MTP/n-gram rejection artifacts.

### Prompt suite

- [ ] Repetitive/code-continuation prompt files committed.
- [ ] Ordinary instruction/chat prompt files committed.
- [ ] Every prompt SHA-256 recorded.
- [ ] Prompt order frozen before the confirmatory run.
- [ ] Token counts recorded with the exact target tokenizer.
- [ ] Sampling parameters, seed, stop conditions, and output limit frozen.

Stock and phase-aware modes must use the same model file, binary, context, prompt/workload, and
sampling configuration. Only the declared CPU phase policy may differ.

## Preflight the phone

- [ ] Device identity and Android build captured.
- [ ] CPU topology and instruction features match the intended target.
- [ ] Vulkan identity/driver captured if the final path uses or discusses Vulkan.
- [ ] Airplane mode on, Wi-Fi off, foreground apps closed, brightness minimum.
- [ ] Battery in declared starting band.
- [ ] Thermal status captured and no adverse transition occurs; do not require absolute `NONE` on
      this phone because status 2 is its observed USB-charging rest state.
- [ ] Starting battery temperature inside the predeclared band:
  `________ °C` to `________ °C`.
- [ ] Starting `MemAvailable` threshold declared:
  `________ kB`.
- [ ] Starting `SwapFree` captured.
- [ ] Model files on device hash to the frozen inputs.
- [ ] Device clock and host clock recorded in UTC.

```bash
export ANDROID_SERIAL=<phone-serial>
adb shell svc power stayon usb
python tools/device_snapshot.py -o /tmp/mobilespec-final-device.json
adb shell 'grep -E "MemAvailable|SwapFree" /proc/meminfo'
adb shell 'dumpsys thermalservice | grep -m1 "Thermal Status"'
adb shell 'dumpsys battery | grep -E "level|temperature|status"'
```

Inspect `/tmp/mobilespec-final-device.json`; do not proceed if identity fields are empty.

## Native-MTP feasibility gate

Run this before implementing or tuning an adaptive policy.

- [x] Target-only mode loads at `-c 512`.
- [x] Target-plus-MTP mode loads at `-c 512`, text-only.
- [x] Peak RSS is recorded for both.
- [x] `MemAvailable` and `SwapFree` are recorded before load, after load, and after generation.
- [ ] The predeclared swap/ZRAM threshold is not crossed.
- [x] Both modes produce non-empty output and every greedy pair matches exactly.
- [x] Speculation is active: 605 proposed, 226 accepted.
- [ ] At least five repetitions per fixed prompt/mode complete.
- [ ] MTP produces a repeatable end-to-end throughput opportunity.

The selected target is 2.19 GB, larger than the 1.84 GB `MemAvailable` observed with the earlier
Llama model resident. Fit is not disproven, but the ZRAM/swap gate cannot be waived.

Apply/check the pinned draft-path fix, validate the suite, and preview it:

```bash
python tools/apply_llama_patch.py --apply
python tools/apply_llama_patch.py
python tools/run_mtp_ab.py --validate-only
python tools/run_mtp_ab.py --dry-run --cooldown 0
```

Measured command:

```bash
export ANDROID_SERIAL=<phone-serial>
python tools/run_mtp_ab.py benchmarks/suites/phase3-mtp.json \
  --serial "$ANDROID_SERIAL" \
  --repetitions 1 \
  --out-dir benchmarks/results/phase3-feasibility
```

Recorded feasibility artifact:

```text
benchmarks/results/phase3-feasibility/20260726-210042-phase3-mtp/mtp-ab.json
sha256: 81e92389437bfe9b02ab7c0b9d5dd41a29f5308bfbbd4711692b4ff114e58932
```

One repetition per prompt completed. Baseline averaged 4.91956 tok/s and MTP 4.82293 tok/s
(0.98036×); acceptance was 37.355%. `SwapFree` dropped by 450,620 kB baseline and 150,028 kB MTP.
ZRAM telemetry was unavailable. The no-swap and 1.03× minimum-speed gates failed; verdict
**fail**. Both sessions began at thermal status 2, so this is feasibility evidence, not a clean
promotion run.

- [x] Failure bundle recorded and hashed.
- [x] Native MTP rejected before a five-repetition promotion run.
- [x] Phase 3 moved to the zero-weight n-gram fallback.
- [x] N-gram device run completed and was rejected because the apparent gain was a persistent-cache
      artifact and its resource gates failed.
- [x] Speculative workstream closed; submission pivoted to phase-aware CPU policy tuning.

## Final phase-aware CPU-policy A/B session

- [x] Autotuner candidate represents a phase pair: prefill `n_threads_batch` plus decode
      `n_threads`; affinity is included only if the runtime applies it correctly per phase.
- [x] Profile identity includes device fingerprint, model SHA-256, llama.cpp build/commit, context,
      pp/tg shape, and workload/scoring class.
- [x] Stale profile identity forces a re-sweep rather than silently reusing a recommendation.
- [x] Confirmatory bundle uses frozen code, binary, model, workload, and policy.
- [x] App and autotuner harnesses counterbalance stock/phase-aware order; device artifacts retained.
- [x] Discarded warm-up is retained separately and explicitly excluded from scored runs.
- [x] The interrupted three-round sweep is rerun to completion and its artifact retained.
- [x] At least five measured samples per phase and mode.
- [x] Prefill tok/s, decode tok/s, and variance are recorded separately.
- [x] Real-generation TTFT, decode tok/s, and end-to-end latency are recorded.
- [x] Individual sample values preserved.
- [x] Peak RSS and swap delta recorded in the final supplemental bundle.
- [x] Thermal and memory start/end recorded per case.
- [x] Exit status/stderr and output artifact retained.
- [x] Failures retained and classified.
- [x] Correctness is unchanged because both modes use the same model/build/sampling contract.
- [x] Sustained generation confirms the selected policy remains beneficial or safely falls back.

Final bundle path or release asset:

```text
benchmarks/results/20260807-real-generation/summary.json
```

Final telemetry/provenance supplement:

```text
benchmarks/results/20260808-final-telemetry/mobilespec-1786188091323.json
sha256: c335c71faed284c33202463582216b94b17ce9f5342a14d410fae76635a7993f
```

Bundle SHA-256:

```text
b2f447f615d51fa4c87084c17b83de78a271685d3ab21f7fe009d40f379c269f
```

The topology-derived sweep is run with:

```bash
python tools/autotune.py --serial "$ANDROID_SERIAL" --force --rounds 3 --cooldown 120 \
  --export-android-policy
```

The final report must come from the phase-pair implementation. Earlier reports from the retired
single-policy candidate model are development evidence only.

## Correctness gate

- [x] Target verification contract documented.
- [x] Fixed-seed target-equivalence test passed where applicable.
- [ ] Coherent-output review passed for every prompt stratum.
- [x] Stop conditions and generated-token counts are valid.
- [x] No malformed output or relevant error log.
- [ ] Perplexity spot-check passed if supported by the selected executable.
- [x] Missing, stale, low-confidence, or failed autotuner profile falls back to stock defaults;
      optimized mode fails closed until an exact policy is present.
- [x] Cancellation and cleanup leave the engine reusable.

Correctness artifact path/hash:

```text
________________________________________
```

## Sustained run

- [x] Combined interleaved baseline/optimized session ran for 14.37 minutes.
- [x] Both modes ran in each of three consecutive no-cooldown suites (15 samples/mode total).
- [x] Repeating prompt schedule and configuration were identical.
- [x] Throughput/TTFT samples retained per request.
- [x] Separate final supplement retains peak RSS and `SwapFree`; older sustained files are clearly
      identified as predating those fields.
- [x] `MemAvailable`, temperature, and thermal status were retained.
- [x] Thermal behavior disclosed: battery temperature 35.5 C to 38.7 C.
- [x] Per-suite results expose drift rather than hiding it in only one aggregate.
- [x] Mode order was counterbalanced inside every suite; a separate reversed-session run was not
      used.

Sustained bundle path/hash:

```text
benchmarks/results/20260807-real-generation/summary.json
b2f447f615d51fa4c87084c17b83de78a271685d3ab21f7fe009d40f379c269f
```

## Android release evidence

- [x] JDK 21.0.11 recorded; Java/Kotlin bytecode target 17.
- [x] Gradle wrapper 8.11.1 recorded.
- [x] Android Gradle Plugin 8.9.1 and Kotlin 2.3.0 recorded.
- [x] compile/target/min SDK 36/36/28, NDK 28.2.13676358, CMake 3.22.1 recorded.
- [x] `./gradlew test assembleDebug --stacktrace` passed.
- [x] Debug APK installed and launched on the target phone.
- [x] JNI library load/native-capabilities startup smoke passed.
- [x] Clean release command documented and passed.
- [x] Release APK path, size, SHA-256, and demo-signing limitation recorded.
- [x] Release APK installed and cold-launched on the target phone.
- [x] Model import verifies and displays SHA-256.
- [x] Chat streams tokens.
- [x] Cancel interrupts generation and the next request works.
- [x] Baseline/optimized selector maps to stock defaults versus the frozen phase pair.
- [x] Benchmark UI shows live progress and before/after metrics.
- [x] Device UI shows the measured device/thermal/memory fields available in the old schema.
- [x] JSON export contains provenance, source/JNI hashes, telemetry, and samples.
- [x] Debug and release APK SHA-256 values recorded.
- [x] Debug-signing/store-distribution limitation disclosed.
- [x] Final build embeds separate app commit, app-source, llama commit, llama-source-diff, and JNI
      library hashes.

Verified release command:

```bash
ANDROID_HOME=/absolute/path/to/Android/Sdk \
  ./gradlew --no-daemon test assembleDebug assembleRelease
```

Verified debug command:

```bash
ANDROID_HOME=/absolute/path/to/Android/Sdk \
  ./gradlew test assembleDebug --stacktrace
```

Verified debug artifact:

```text
app/build/outputs/apk/debug/app-debug.apk
size:   39,284,593 bytes
sha256: 6228b85028a8f016ea6463f420da0a47e66264b87c3e3c8944922b66fcc87385
```

Release APK path and SHA-256:

```text
app/build/outputs/apk/release/app-release.apk
size:   28,836,295 bytes
sha256: 345c2d81fdf1a921b3617976ca9daaf2a82f5d0b3588c4a90c79719be2aa1a0b
```

Both final APKs verify with APK Signature Scheme v2. The release APK intentionally uses debug
signing for the demo. The phase-aware debug APK passed the complete device flow; the new telemetry
build is installed on the target phone and its supplemental device export passed.

## Headline chart

Create the chart only from the frozen final bundle.

- [x] Baseline and optimized mean shown.
- [x] Variability/error bars shown.
- [x] `N`, workload, model, quantization, device, and metric named on-chart or in adjacent caption.
- [x] Source bundle path/hash shown adjacent in the README.
- [x] TTFT shown separately from decode throughput.
- [x] Sustained behavior shown.
- [x] No exploratory run mixed into the confirmatory aggregation.
- [x] PNG generated at `docs/assets/mobilespec-phase-policy.png`.

If the phase pair fails its gate, replace the speedup chart with an honest decision chart showing
how the controller rejected contaminated/slower candidates and retained stock defaults.

## Final claim

Fill only after all referenced fields are backed by artifacts:

> On **[device and SoC]** running **[Android/driver]**, with
> **[model file, quantization, SHA-256 prefix]** at **[context]**, MobileSpec's
> **[exact policy]** changed **[metric]** from **[baseline mean ± std]** to
> **[optimized mean ± std]** across **[N]** repetitions of **[prompt/workload]**
> (**[relative change]%**), while **[correctness contract]** passed,
> **[peak RSS/swap outcome]**, and **[sustained-run outcome]**.

- [ ] Every noun and number above maps to a raw artifact.
- [ ] README uses the same values.
- [ ] Video overlay uses the same values.
- [ ] Devpost text uses the same values.

## Demo video, under three minutes

No video exists at the current evidence boundary.

| Time | Required shot/evidence |
|---|---|
| 0:00–0:20 | Problem: decode is bandwidth-bound on a real Arm phone |
| 0:20–0:45 | Physical target device, SoC/model/quantization, no cloud inference |
| 0:45–1:35 | Live stock-versus-phase-aware run with visible TTFT and tok/s |
| 1:35–2:10 | Topology discovery plus separate prefill/decode policy selection |
| 2:10–2:40 | Sustained and memory result from the frozen evidence bundle |
| 2:40–3:00 | Public repository, exact reproduce entry point, license |

- [ ] No terminal-only result is substituted for the visible app if the app is claimed.
- [ ] Recording shows the same frozen model/policy used in evidence.
- [ ] Claims remain legible on a phone-sized player.
- [ ] Video duration is under 3:00.
- [ ] Upload succeeds and unlisted/public URL opens in a logged-out browser.

Video URL:

```text
[BLOCKED — not recorded/uploaded]
```

## Fresh-clone test

Run on a clean path, not the development worktree.

- [ ] Public clone works without private credentials.
- [x] Recursive shallow submodules resolve at the documented pins.
- [ ] README model download/hash step passes.
- [ ] CPU and Vulkan build commands pass.
- [ ] Smoke generation is coherent.
- [ ] Standard suite emits/summarizes 12 results.
- [ ] Historical MTP and n-gram rejection artifacts remain inspectable; rerunning them is not a
      submission prerequisite.
- [ ] Phase-aware autotuner runs as documented and either selects a gated winner or retains stock.
- [x] Android release command produces an APK from the clean clone.
- [ ] Exported result can be traced to code, model, device, and raw samples.
- [ ] Temporary stay-awake state is cleared after the device test.

Fresh-clone environment, date, commit, and log:

```text
8 Aug 2026, `/tmp/llama-edge-shallow.QAqcVr`
commit `c854112163e1`; llama.cpp `178a6c449371`; Vulkan-Headers `8864cdc896bb`
`./gradlew --no-daemon test assembleRelease`: BUILD SUCCESSFUL in 54s, 146 tasks
```

## Release and submission

- [ ] Root `LICENSE` is Apache-2.0 and visible.
- [ ] Third-party/model license boundaries are documented.
- [ ] Repository is public.
- [ ] Default branch points at the frozen submission commit.
- [ ] GitHub Release contains immutable evidence bundle and APK, if distributing it.
- [ ] Release asset SHA-256 values recorded.
- [ ] README chart, demo URL, exact reproduction, methodology, and limitations are final.
- [ ] `CHANGES_FOR_CHALLENGE.md` includes Phase 3–5 completed work.
- [ ] Devpost Mobile AI entry is registered.
- [ ] Devpost repo and video links open while logged out.
- [ ] Submission completed before the internal 12 August target.
- [ ] Submission confirmation URL/timestamp captured.

Release URL:

```text
________________________________________
```

Devpost URL and submitted UTC timestamp:

```text
________________________________________
```

## Session cleanup

```bash
adb shell svc power stayon false
```

- [ ] Stay-awake override cleared.
- [ ] Device released from USB/benchmark state.
- [ ] Battery level and temperature recorded.
- [ ] Raw artifacts copied and hashed before any cleanup.
- [ ] Final working tree and remote state inspected.
