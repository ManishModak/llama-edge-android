#!/usr/bin/env bash
# Phase 2 profiling: per-core thread placement during llama.cpp CPU decode.
#
# Tests the "A55 straggler" hypothesis behind the non-monotonic decode curve
# (t=4 is 15% slower than t=2 — see docs/baseline-results.md observation 3).
#
# NOTE ON simpleperf: not usable on this device. The MT6855 kernel refuses
# perf_event_open for every event type — hardware AND software — even with
# /proc/sys/kernel/perf_event_paranoid = -1:
#     simpleperf E event_selection_set.cpp:262]
#         Event type 'cpu-cycles' is not supported on the device
# `simpleperf list` advertises the PMU events, but opening any of them fails.
# So symbol-level sampling is unavailable and we rely on:
#   1. /proc/<pid>/task/*/stat field 39 (processor) — thread->core placement
#   2. fields 14/15 (utime/stime) — per-thread CPU time, exposes stragglers
#   3. Perfetto ftrace sched tracks — full switch/freq timeline for the record
#
# Usage: tools/profile_decode.sh [thread-counts...]     (default: 2 4 6)

set -euo pipefail

: "${ANDROID_SERIAL:?Set ANDROID_SERIAL to the target phone shown by adb devices}"
SERIAL="$ANDROID_SERIAL"
DEV=/data/local/tmp/llama-edge
MODEL=$DEV/models/Llama-3.2-1B-Instruct-Q4_0.gguf
OUT="${OUT:-benchmarks/results/profiles/$(date -u +%Y%m%d-%H%M%S)}"
[ $# -gt 0 ] && THREADS=("$@") || THREADS=(2 4 6)

# Decode-only, single rep — we want a clean placement sample, not a timing.
BENCH="./llama-bench -m $MODEL -p 0 -n 128 -r 1"

adb() { command adb -s "$SERIAL" "$@"; }

mkdir -p "$OUT"
echo "==> output: $OUT"
echo "==> cores: cpu0-5 = Cortex-A55 (LITTLE), cpu6-7 = Cortex-A78 (big)"

# Perfetto insists on reading its config from /data/misc/perfetto-configs.
adb push tools/perfetto/decode-trace.pbtxt /data/misc/perfetto-configs/ >/dev/null

for t in "${THREADS[@]}"; do
    echo "==> t=$t : sampling thread->core placement"

    # Sampler runs on-device: poll every thread's `processor` field for the life
    # of the benchmark, emitting "tid comm processor utime stime".
    #
    # Kept to exactly two processes per sample (one cat over all task/*/stat,
    # one awk). Sampling per-thread would spawn N processes per sample and the
    # sampler would contend with the workload it is trying to measure.
    adb shell "cd $DEV && {
        $BENCH -t $t > bench-t$t.txt 2>&1 &
        BP=\$!
        : > sched-t$t.txt
        while kill -0 \$BP 2>/dev/null; do
            cat /proc/\$BP/task/*/stat 2>/dev/null \
                | awk '{print \$1, \$2, \$39, \$14, \$15} END {print \"---\"}'
            sleep 0.05
        done >> sched-t$t.txt
        wait \$BP
    }" 2>&1 | tail -2

    adb pull "$DEV/sched-t$t.txt" "$OUT/" >/dev/null 2>&1
    adb pull "$DEV/bench-t$t.txt" "$OUT/" >/dev/null 2>&1

    echo "==> t=$t : cooldown 45s"
    sleep 45
done

# One full Perfetto trace at the anomalous thread count, as a shareable artifact.
ANOM=${ANOM:-4}
echo "==> t=$ANOM : perfetto sched+freq trace"
adb shell "perfetto -c /data/misc/perfetto-configs/decode-trace.pbtxt --txt \
    -o /data/misc/perfetto-traces/decode-t$ANOM.pftrace" >/dev/null 2>&1 &
PF=$!
sleep 2
adb shell "cd $DEV && $BENCH -t $ANOM" > "$OUT/bench-perfetto-t$ANOM.txt" 2>&1
wait $PF || true
adb pull "/data/misc/perfetto-traces/decode-t$ANOM.pftrace" "$OUT/" >/dev/null 2>&1 || true

echo "==> done: $OUT"
echo "    analyse with: tools/analyze_placement.py $OUT"
