import json, re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from ods import sheets
src, out = sys.argv[1], sys.argv[2]
data = {name: rows for name, rows in sheets(src) if name.startswith("1415")}
ee, ic = data["1415a_Entries_and_Exits"], data["1415b_Interchanges"]
def table(rows):
    head = next(i for i, r in enumerate(rows) if r and r[0] == "Sort")
    return rows[head], rows[head + 1:]
hdr, rows = table(ee)
years = [h for h in hdr[6:] if h.startswith("Apr")]
labels = [re.sub(r"Apr (\d{4}) to Mar (\d{4})", lambda m: f"{m.group(1)}-{m.group(2)[2:]}", y).split(" [")[0] for y in years]
num = lambda v: int(round(float(v))) if re.fullmatch(r"-?\d+(\.\d+)?", v or "") else None
ihdr, irows = table(ic)
inter = {r[2]: num(r[6 + len(years) - 1]) if len(r) > 6 + len(years) - 1 else None for r in irows if len(r) > 2}
stations = {}
for r in rows:
    if len(r) < 7 or not re.fullmatch(r"[A-Z]{3}", r[2] or ""):
        continue
    vals = [num(r[6 + i]) if 6 + i < len(r) else None for i in range(len(years))]
    series = {labels[i]: vals[i] for i in range(len(years) - 10, len(years)) if vals[i] is not None}
    if not series:
        continue
    stations[r[2]] = {"name": r[1], "region": r[4], "district": r[5].split(" - ")[0], "usage": series,
                      "interchanges": inter.get(r[2])}
latest = labels[-1]
ranked = sorted((c for c in stations if stations[c]["usage"].get(latest)), key=lambda c: -stations[c]["usage"][latest])
for i, c in enumerate(ranked, 1):
    stations[c]["rank"] = i
json.dump({"source": "Office of Rail and Road, Estimates of station usage, table 1415",
           "licence": "Open Government Licence v3.0", "latest": latest, "ranked": len(ranked),
           "stations": stations}, open(out, "w"), separators=(",", ":"))
print(len(stations), "stations; latest", latest, "; top", [(c, stations[c]["usage"][latest]) for c in ranked[:3]])
print("HNK", stations.get("HNK"))
