# Public Repository Audit

- Audit date: 8 August 2026
- Audited branch base: `agent/kleidiai-gpu-policy` at `ed5b726`

Scope: current tracked tree, reachable local Git history, submodule pins, release inputs, and public
documentation.

## Result

No credential-pattern match, private key, keystore, model weight, APK/AAB, or other release binary
is tracked. The repository is small enough for a normal recursive clone, both submodule URLs use
public HTTPS endpoints, and the root Apache-2.0 license is present. The privacy and notice findings
below were corrected in the live tree.

This audit is evidence from explicit checks, not a guarantee that an arbitrary secret scanner can
never find another pattern.

## Checks performed

| Area | Evidence | Result |
|---|---|---|
| Common credentials/private keys | Filename-only current-tree scan plus `git log -G` across all reachable refs for AWS, GitHub, Google API, OpenAI-style keys, PEM private-key headers, API-key/client-secret/password assignments | No matches |
| Sensitive files | Tracked extensions checked for `.jks`, `.keystore`, `.pem`, `.p12`, `.apk`, `.aab`, `.gguf`, `.bin`, and `.safetensors` | None tracked |
| Model redistribution | `git ls-files` plus manifest inspection | Only source URLs, sizes, licenses, and SHA-256 identities are tracked; no weights |
| Large blobs | `git ls-tree -rl HEAD` and object-store size | Largest tracked blob is the 2,084,932-byte compressed Perfetto trace; repository pack data is about 2.6 MiB excluding submodules |
| Remote/submodules | `.gitmodules`, `git remote -v`, and `git submodule status --recursive` | Public HTTPS URLs; llama.cpp and Vulkan-Headers pins resolve exactly |
| Signing | Gradle configuration and tracked-file scan | Release demo uses the external Gradle debug keystore; no key material is tracked; not a store-signing claim |
| Third-party licensing | llama.cpp, Vulkan-Headers, fetched KleidiAI archive, Maven POMs, and NDK LLVM notice | Inventory recorded in `THIRD_PARTY_NOTICES.md`; release must include the matching NDK notice |

## Privacy cleanup

The physical phone's ADB serial and developer-specific Linux paths were present in active docs,
one profiler default, and a unit-test fixture. Active docs/tools now use `<phone-serial>`,
`$ANDROID_HOME`, `LLAMA_EDGE_MODELS`, or synthetic fixture values. Device model, SoC, GPU, driver,
and measured hardware properties remain because they are necessary experiment identity.

The serial and one historical absolute source path still exist in already-published Git history and
immutable raw benchmark/autotuner JSON. They are not authentication secrets, and changing those
artifacts would invalidate documented hashes. This audit therefore does not rewrite public history
or alter raw evidence. Future captures must use a redacted public-device label rather than a
physical ADB serial in exported reports.

## Release gate

Before publication, rerun the checks from a clean final commit and verify that the release bundle
contains the APK, root license, third-party notices, matching NDK LLVM notice, source identities,
and SHA-256 manifest. A public clean-clone build and physical model/import smoke remain separate
pending gates; this source audit does not prove them.
