#!/usr/bin/env python3
"""Analyse thread->core placement samples from tools/profile_decode.sh.

Answers the Phase 2 question: when llama.cpp runs N unpinned decode threads on
the MT6855's 2xA78 + 6xA55 topology, where do the threads actually land, and
does the work split evenly?

Input:  <dir>/sched-t<N>.txt, produced by profile_decode.sh. Each sample block
        is a set of lines "tid comm processor utime stime", terminated by "---".
Output: per-thread core residency and CPU-time spread, per thread count.

Usage: tools/analyze_placement.py benchmarks/results/profiles/<timestamp>
"""

import sys
import re
from pathlib import Path
from collections import defaultdict

BIG = {6, 7}           # Cortex-A78
LITTLE = {0, 1, 2, 3, 4, 5}  # Cortex-A55
HZ = 100               # kernel USER_HZ; utime/stime are in these ticks


def parse(path):
    """Return {tid: {"comm": str, "cpus": Counter, "utime": int, "stime": int}}."""
    threads = defaultdict(
        lambda: {"comm": "?", "cpus": defaultdict(int), "utime": 0, "stime": 0}
    )
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line == "---":
            continue
        # comm can contain spaces inside parens: "1234 (llama bench) 6 100 20"
        m = re.match(r"^(\d+)\s+\((.*)\)\s+(\d+)\s+(\d+)\s+(\d+)$", line)
        if not m:
            continue
        tid, comm, cpu, utime, stime = (
            int(m.group(1)), m.group(2), int(m.group(3)),
            int(m.group(4)), int(m.group(5)),
        )
        t = threads[tid]
        t["comm"] = comm
        t["cpus"][cpu] += 1
        # utime/stime are cumulative — keep the max (final) value.
        t["utime"] = max(t["utime"], utime)
        t["stime"] = max(t["stime"], stime)
    return threads


def report(nthreads, threads):
    print(f"\n{'='*72}")
    print(f"  t={nthreads}  ({len(threads)} threads observed)")
    print(f"{'='*72}")

    # Only threads that actually burned CPU are ggml workers; the rest are
    # bookkeeping threads that would dilute the picture.
    workers = {
        tid: t for tid, t in threads.items() if (t["utime"] + t["stime"]) > 0
    }
    if not workers:
        print("  no CPU-consuming threads sampled")
        return

    print(f"\n  {'tid':>7}  {'comm':<16} {'CPU s':>7}  {'big%':>6}  core residency")
    print(f"  {'-'*7}  {'-'*16} {'-'*7}  {'-'*6}  {'-'*28}")

    cpu_times = []
    for tid, t in sorted(workers.items(), key=lambda kv: -sum(kv[1]["cpus"].values())):
        total = sum(t["cpus"].values())
        big = sum(n for c, n in t["cpus"].items() if c in BIG)
        secs = (t["utime"] + t["stime"]) / HZ
        cpu_times.append(secs)
        resid = " ".join(
            f"cpu{c}:{100*n//total}%"
            for c, n in sorted(t["cpus"].items(), key=lambda kv: -kv[1])[:4]
        )
        print(f"  {tid:>7}  {t['comm']:<16} {secs:>7.2f}  {100*big/total:>5.1f}%  {resid}")

    # The straggler signal: if the split were fair, every worker would burn the
    # same CPU time. Spread is what stalls the pool at each per-op barrier.
    lo, hi = min(cpu_times), max(cpu_times)
    print(f"\n  CPU-time spread across workers: min {lo:.2f}s  max {hi:.2f}s", end="")
    print(f"  ratio {hi/lo:.2f}x" if lo > 0 else "")

    # Aggregate placement.
    allcpu = defaultdict(int)
    for t in workers.values():
        for c, n in t["cpus"].items():
            allcpu[c] += n
    tot = sum(allcpu.values())
    big = sum(n for c, n in allcpu.items() if c in BIG)
    lit = sum(n for c, n in allcpu.items() if c in LITTLE)
    print(f"  aggregate residency: A78(big) {100*big/tot:.1f}%   "
          f"A55(LITTLE) {100*lit/tot:.1f}%")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = Path(sys.argv[1])
    files = sorted(d.glob("sched-t*.txt"),
                   key=lambda p: int(re.search(r"t(\d+)", p.name).group(1)))
    if not files:
        sys.exit(f"no sched-t*.txt under {d}")
    for f in files:
        n = int(re.search(r"t(\d+)", f.name).group(1))
        report(n, parse(f))
    print()


if __name__ == "__main__":
    main()
