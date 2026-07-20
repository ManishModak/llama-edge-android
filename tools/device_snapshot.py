#!/usr/bin/env python3
"""Capture device identity/state via adb into a JSON snapshot.

Usage: python tools/device_snapshot.py [-o out.json]
Works on Windows and Linux (adb must be on PATH).
"""
import argparse
import datetime
import json
import subprocess
import sys


def adb(*args: str) -> str:
    try:
        r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except FileNotFoundError:
        sys.exit("adb not found on PATH")


def getprop(name: str) -> str:
    return adb("shell", "getprop", name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=None, help="output JSON path")
    args = ap.parse_args()

    devices = [l for l in adb("devices").splitlines()[1:] if l.strip().endswith("device")]
    if not devices:
        sys.exit("no device connected")

    snap = {
        "timestampUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "serial": devices[0].split()[0],
        "model": getprop("ro.product.model"),
        "soc": getprop("ro.soc.model"),
        "socManufacturer": getprop("ro.soc.manufacturer"),
        "android": getprop("ro.build.version.release"),
        "abilist": getprop("ro.product.cpu.abilist"),
        "cpuFeatures": adb("shell", "grep -m1 Features /proc/cpuinfo"),
        "memTotalKb": adb("shell", "grep MemTotal /proc/meminfo"),
        "memAvailableKb": adb("shell", "grep MemAvailable /proc/meminfo"),
        "thermal": adb("shell", "dumpsys thermalservice | grep -m1 -i status"),
        "battery": adb("shell", "dumpsys battery | grep -E 'level|temperature|status'"),
    }

    text = json.dumps(snap, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
