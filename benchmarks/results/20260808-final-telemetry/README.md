# Final telemetry and provenance supplement

`mobilespec-1786188091323.json` is the accepted supplement. It was captured from the final debug
build after runtime JNI hashing was corrected to read the library directly from the APK when
Android does not extract native libraries. It contains five native-timing runs per mode, exact
output-hash correctness, complete source/JNI hashes, process VmRSS/VmHWM, and SwapFree telemetry.

```text
artifact sha256: c335c71faed284c33202463582216b94b17ce9f5342a14d410fae76635a7993f
app source:       239ed059e829ee68afbc14cf0fe853b5c6b1d47d31e242315ed7ce77e63534d6
JNI library:      1e50ca51c1228862f349232c6ffb0061e9edc6c10b682dc1e5e37998f96c3251
baseline decode:  6.0571 tok/s
optimized decode: 10.6603 tok/s
peak VmHWM:       1,760,477,184 bytes
temperature:      35.2 C to 37.6 C
```

SwapFree fell by 404,439,040 bytes during the discarded warm-up, then recovered by 66,021,376
bytes across the ten measured runs. Per-run SwapFree deltas summed to +55,472,128 bytes for
baseline and +10,592,256 bytes for optimized. This is system-wide telemetry, not attribution of
swap activity to either mode; it shows no additional cumulative SwapFree loss during the scored
A/B portion.

`mobilespec-1786187371057.json` is retained as a diagnostic failure. It exposed that hashing only
`applicationInfo.nativeLibraryDir` returned null on an install where Android loaded JNI directly
from the APK. Do not use it as the final provenance supplement.
