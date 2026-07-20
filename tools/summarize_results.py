#!/usr/bin/env python3
"""Summarize a run_suite results dir into a markdown table.

Usage:
  python tools/summarize_results.py benchmarks/results/raw/<timestamp>-<suite> [-o out.md]

Columns: case, backend, threads, pp tok/s, tg tok/s (mean +/- std), thermal start->end.
Works on an empty/dry-run dir (prints a clear message). stdlib only, Windows + Linux.
"""
import argparse
import json
import sys
from pathlib import Path


def fmt_stat(stat: dict | None) -> str:
    if not stat or stat.get("mean") is None:
        return "-"
    mean = stat["mean"]
    std = stat.get("std")
    return f"{mean:.2f} +/- {std:.2f}" if std is not None else f"{mean:.2f}"


def fmt_thermal(dev: dict) -> str:
    def temp(node):
        t = (dev.get(node) or {}).get("batteryTempC")
        return f"{t:.1f}C" if isinstance(t, (int, float)) else "?"
    start, end = temp("thermalStart"), temp("thermalEnd")
    if start == "?" and end == "?":
        return "-"
    return f"{start} -> {end}"


def load_results(results_dir: Path) -> list[dict]:
    rows = []
    for p in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("schemaVersion") == 1 and "caseId" in data:
            rows.append(data)
    return rows


def build_table(rows: list[dict]) -> str:
    header = ("| case | backend | threads | pp tok/s | tg tok/s | thermal (batt) |\n"
              "|---|---|---|---|---|---|")
    lines = [header]
    dry = False
    for r in rows:
        m = r.get("metrics", {})
        run = r.get("run", {})
        dry = dry or r.get("dryRun", False)
        lines.append("| {case} | {backend} | {threads} | {pp} | {tg} | {th} |".format(
            case=r.get("caseId", "?"),
            backend=run.get("backend", "?"),
            threads=run.get("threads", "?"),
            pp=fmt_stat(m.get("ppTokensPerSec")),
            tg=fmt_stat(m.get("tgTokensPerSec")),
            th=fmt_thermal(r.get("device", {})),
        ))
    out = "\n".join(lines)
    if dry:
        out += "\n\n> Note: this dir contains dry-run placeholders (no measured metrics)."
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize run_suite results into markdown.")
    ap.add_argument("results_dir", help="a benchmarks/results/raw/<timestamp>-<suite> dir")
    ap.add_argument("-o", "--out", default=None, help="write markdown to this file instead of stdout")
    args = ap.parse_args()

    d = Path(args.results_dir)
    if not d.is_dir():
        sys.exit(f"not a directory: {d}")

    rows = load_results(d)
    if not rows:
        msg = f"No schemaVersion-1 result JSON found in {d}. Run tools/run_suite.py first."
        if args.out:
            Path(args.out).write_text(msg + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(msg)
        return

    suite = rows[0].get("suite", d.name)
    md = f"# Results: {suite} ({len(rows)} case(s))\n\n" + build_table(rows) + "\n"
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
