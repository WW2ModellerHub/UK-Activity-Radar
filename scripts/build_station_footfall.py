"""Build app/assets/station_footfall.json from Network Rail's daily concourse footfall.

Network Rail counts people entering its managed stations' concourses each day
(Rail Data Marketplace, "NWR Daily Concourse Footfall"). Kept: for the latest
full year, the average entries per day of the week and the busiest day.

    python scripts/build_station_footfall.py DailyConcourseFootfall_NR_2025.csv
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app" / "assets" / "station_footfall.json"
CRS = {"Leeds": "LDS", "Reading": "RDG", "King's Cross": "KGX", "Liverpool Street": "LST", "Waterloo": "WAT",
       "Bristol Temple Meads": "BRI", "Glasgow Central": "GLC", "Charing Cross": "CHX", "Euston": "EUS",
       "Liverpool Lime Street": "LIV", "Guildford": "GLD", "Paddington": "PAD", "Edinburgh Waverley": "EDB",
       "Birmingham New Street": "BHM", "London Bridge": "LBG", "Manchester Piccadilly": "MAN",
       "Cannon Street": "CST", "Victoria": "VIC"}


def build(path: str) -> dict:
    days: dict[str, dict[date, int]] = defaultdict(dict)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            crs = CRS.get((row.get("SiteName") or "").strip())
            try:
                day, count = date.fromisoformat(row["DateFrom"]), int(row["IN"])
            except (KeyError, TypeError, ValueError):
                continue
            if crs and count > 0:
                days[crs][day] = days[crs].get(day, 0) + count
    stations = {}
    for crs, counts in days.items():
        if len(counts) < 300:      # a full year only
            continue
        by_weekday = defaultdict(list)
        for day, count in counts.items():
            by_weekday[day.weekday()].append(count)
        busiest = max(counts, key=counts.get)
        stations[crs] = {
            "weekday": [round(sum(v) / len(v)) for _, v in sorted(by_weekday.items())],   # Monday first
            "busiest_day": busiest.isoformat(), "busiest": counts[busiest],
        }
    year = max(max(c) for c in days.values()).year
    return {"year": year, "source": "Network Rail daily concourse footfall (Rail Data Marketplace)", "stations": stations}


if __name__ == "__main__":
    data = build(sys.argv[1])
    OUT.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(data['stations'])} stations, {data['year']} -> {OUT}")
