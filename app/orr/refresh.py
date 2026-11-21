"""Keep the official ORR figures current without a deploy.

Weekly: punctuality and cancellations (tables 3138 and 3124, four-weekly).
Monthly: station usage (table 1415, yearly). Each download is rebuilt to the
same JSON the pages read, saved on the data disk, and swapped in live. A
failed or odd download keeps the figures already in use.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path

import requests

PERFORMANCE_CSV = "https://dataportal.orr.gov.uk/umbraco/api/databricksreport/downloadcsv?contentId={id}"
USAGE_PAGE = "https://dataportal.orr.gov.uk/statistics/usage/estimates-of-station-usage/table-1415-time-series-of-passenger-entries-and-exits-and-interchanges-by-station/"
HEADERS = {"User-Agent": "UKActivityRadar/1.5 (ORR open data)"}
_num = lambda v: float(v) if re.fullmatch(r"-?\d+(\.\d+)?", (v or "").strip()) else None


def _period(label: str) -> str:
    m = re.match(r"Apr (\d{4}) to Mar (\d{4}) \(Period (\d+)\)", label)
    return f"{m.group(1)}-{m.group(2)[2:]} P{int(m.group(3))}" if m else label


def build_performance(punct_csv: str, canc_csv: str) -> dict:
    rows: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(punct_csv.lstrip("﻿"))):
        rows.setdefault(r["Operator"], {})[r["Time Period"]] = {
            "on_time": _num(r["On Time Within 59 Seconds"]), "within_3": _num(r["Time To 3"]),
            "within_15": _num(r["Time To 15"]), "on_time_year": _num(r["On Time Within 59 Seconds Maa"])}
    for r in csv.DictReader(io.StringIO(canc_csv.lstrip("﻿"))):
        rows.setdefault(r["National Or Operator"], {}).setdefault(r["Time Period"], {}).update(
            {"cancelled": _num(r["Periodic Cancellations Percentage"]),
             "cancelled_year": _num(r["Moving Annual Average Cancellations Percentage"])})
    operators = {}
    for name, periods in rows.items():
        keys = [k for k in periods if periods[k].get("on_time") is not None][-13:]
        if keys:
            operators[name] = [{"period": _period(k), **periods[k]} for k in keys]
    return {"source": "Office of Rail and Road, tables 3138 and 3124 (four-weekly railway periods)",
            "licence": "Open Government Licence v3.0", "operators": operators}


def build_usage(ods_bytes: bytes) -> dict:
    from .ods import sheets_from_bytes
    data = {name: rows for name, rows in sheets_from_bytes(ods_bytes) if name.startswith("1415")}
    def table(rows):
        head = next(i for i, r in enumerate(rows) if r and r[0] == "Sort")
        return rows[head], rows[head + 1:]
    hdr, rows = table(data["1415a_Entries_and_Exits"])
    years = [h for h in hdr[6:] if h.startswith("Apr")]
    labels = [re.sub(r"Apr (\d{4}) to Mar (\d{4})", lambda m: f"{m.group(1)}-{m.group(2)[2:]}", y).split(" [")[0] for y in years]
    num = lambda v: int(round(float(v))) if re.fullmatch(r"-?\d+(\.\d+)?", v or "") else None
    _, irows = table(data["1415b_Interchanges"])
    inter = {r[2]: num(r[6 + len(years) - 1]) if len(r) > 6 + len(years) - 1 else None for r in irows if len(r) > 2}
    stations = {}
    for r in rows:
        if len(r) < 7 or not re.fullmatch(r"[A-Z]{3}", r[2] or ""):
            continue
        vals = [num(r[6 + i]) if 6 + i < len(r) else None for i in range(len(years))]
        series = {labels[i]: vals[i] for i in range(max(0, len(years) - 10), len(years)) if vals[i] is not None}
        if series:
            stations[r[2]] = {"name": r[1], "region": r[4], "district": r[5].split(" - ")[0], "usage": series,
                              "interchanges": inter.get(r[2])}
    latest = labels[-1]
    ranked = sorted((c for c in stations if stations[c]["usage"].get(latest)), key=lambda c: -stations[c]["usage"][latest])
    for i, c in enumerate(ranked, 1):
        stations[c]["rank"] = i
    return {"source": "Office of Rail and Road, Estimates of station usage, table 1415",
            "licence": "Open Government Licence v3.0", "latest": latest, "ranked": len(ranked), "stations": stations}


def refresh_performance(data_dir: Path, session=None) -> bool:
    session = session or requests.Session()
    punct = session.get(PERFORMANCE_CSV.format(id=1681), headers=HEADERS, timeout=60)
    canc = session.get(PERFORMANCE_CSV.format(id=1683), headers=HEADERS, timeout=60)
    punct.raise_for_status(); canc.raise_for_status()
    built = build_performance(punct.text, canc.text)
    if len(built["operators"]) < 15 or "Great Britain" not in built["operators"]:
        return False   # an odd download: keep what we have
    _save(data_dir / "operator_performance.json", built)
    from .. import seo_pages
    seo_pages._PERFORMANCE = built
    return True


def refresh_usage(data_dir: Path, session=None) -> bool:
    session = session or requests.Session()
    page = session.get(USAGE_PAGE, headers=HEADERS, timeout=60)
    page.raise_for_status()
    match = re.search(r'href="(/media/[^"]+?\.ods)', page.text)
    if not match:
        return False
    ods = session.get("https://dataportal.orr.gov.uk" + match.group(1), headers=HEADERS, timeout=120)
    ods.raise_for_status()
    built = build_usage(ods.content)
    if len(built["stations"]) < 2000:
        return False
    _save(data_dir / "station_usage.json", built)
    from .. import seo_pages
    seo_pages._USAGE = built
    return True


def load_saved(data_dir: Path) -> None:
    """At start, prefer copies refreshed earlier over the bundled ones."""
    from .. import seo_pages
    for name, attr in (("operator_performance.json", "_PERFORMANCE"), ("station_usage.json", "_USAGE")):
        try:
            setattr(seo_pages, attr, json.loads((data_dir / name).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def due(path: Path, days: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime > days * 86400
    except OSError:
        return True
