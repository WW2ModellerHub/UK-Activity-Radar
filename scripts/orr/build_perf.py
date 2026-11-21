import csv, json, re, sys
punct, canc, out = sys.argv[1:4]
def period(label):
    m = re.match(r"Apr (\d{4}) to Mar (\d{4}) \(Period (\d+)\)", label)
    return f"{m.group(1)}-{m.group(2)[2:]} P{int(m.group(3))}" if m else label
num = lambda v: float(v) if re.fullmatch(r"-?\d+(\.\d+)?", (v or "").strip()) else None
rows = {}
for r in csv.DictReader(open(punct, encoding="utf-8-sig")):
    rows.setdefault(r["Operator"], {})[r["Time Period"]] = {
        "on_time": num(r["On Time Within 59 Seconds"]), "within_3": num(r["Time To 3"]),
        "within_15": num(r["Time To 15"]), "on_time_year": num(r["On Time Within 59 Seconds Maa"])}
for r in csv.DictReader(open(canc, encoding="utf-8-sig")):
    rows.setdefault(r["National Or Operator"], {}).setdefault(r["Time Period"], {}).update(
        {"cancelled": num(r["Periodic Cancellations Percentage"]),
         "cancelled_year": num(r["Moving Annual Average Cancellations Percentage"])})
operators = {}
for name, periods in rows.items():
    keys = [k for k in periods if periods[k].get("on_time") is not None][-13:]
    if not keys:
        continue
    operators[name] = [{"period": period(k), **periods[k]} for k in keys]
json.dump({"source": "Office of Rail and Road, tables 3138 and 3124 (four-weekly railway periods)",
           "licence": "Open Government Licence v3.0", "operators": operators}, open(out, "w"), separators=(",", ":"))
print(len(operators), "operators; latest", operators["Great Britain"][-1])
