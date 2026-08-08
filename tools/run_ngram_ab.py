#!/usr/bin/env python3
"""Entrypoint for the Llama 1B zero-weight ngram-mod fallback A/B."""

from pathlib import Path

from run_spec_ab import main


SUITE = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "suites"
    / "phase3-ngram.json"
)


if __name__ == "__main__":
    raise SystemExit(main(SUITE))
