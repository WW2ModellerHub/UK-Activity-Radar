"""Build app/reference/running_allowances.json from a Network Rail PIF.

The Planning Infrastructure File (Rail Data Marketplace) gives sectional
running times (TLK records) for each link between timing points, by
traction, with entry and exit speed: -1 = passing at line speed, 0 = from or
to a stand. The difference between "start from a stand" and "passing" is how
long that traction loses accelerating; "stop" minus "passing" is braking.

Only those averages per traction are kept (a few KB), not the file itself.

    python scripts/build_running_allowances.py PIF20261120571120089.txt.gz
"""
from __future__ import annotations

import collections
import gzip
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "reference" / "running_allowances.json"
MIN_LINKS = 30


def _seconds(text: str) -> int:
    minutes, seconds = text.strip().lstrip("+").split("'")
    return int(minutes) * 60 + int(seconds)


def build(path: str) -> dict:
    links: dict[tuple, dict] = collections.defaultdict(dict)
    with gzip.open(path, "rt", encoding="latin-1") as handle:
        for line in handle:
            if not line.startswith("TLK\t"):
                continue
            f = line.rstrip("\r\n").split("\t")
            if len(f) < 14 or f[4].strip() == "BUS" or f[5].startswith("BUS"):
                continue
            try:
                runtime = _seconds(f[13])
            except ValueError:
                continue
            key = (f[2], f[3], f[4].strip(), f[5].strip(), f[6].strip(), f[7].strip())
            links[key][(f[9].strip(), f[10].strip())] = runtime
    start, stop = collections.defaultdict(list), collections.defaultdict(list)
    for (_o, _d, _line, traction, load, _speed), times in links.items():
        passing = times.get(("-1", "-1"))
        if not passing:
            continue
        keys = [f"traction:{traction}"] + ([f"load:{load}"] if load.isdigit() else [])
        for name in keys:
            if ("0", "-1") in times:
                start[name].append(max(0, times[("0", "-1")] - passing))
            if ("-1", "0") in times:
                stop[name].append(max(0, times[("-1", "0")] - passing))
    table = {}
    for name in sorted(set(start) | set(stop)):
        a, b = start.get(name, []), stop.get(name, [])
        if len(a) >= MIN_LINKS and len(b) >= MIN_LINKS:
            # Seconds lost to starting / stopping, averaged, to the nearest 5 s.
            table[name] = [round(sum(a) / len(a) / 5) * 5, round(sum(b) / len(b) / 5) * 5]
    return table


if __name__ == "__main__":
    table = build(sys.argv[1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "Network Rail Planning Infrastructure File (sectional running times)",
                               "allowances_s": table}, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"{len(table)} tractions -> {OUT}")
