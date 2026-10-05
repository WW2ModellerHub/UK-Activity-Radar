from app.collectors.bods import BODSCollector, parse_bods_payload
from app.config import settings
from app.state import state

print("Testing DfT Bus Open Data Service…")
collector = BODSCollector(settings, state)
payload, content_type, used_mode = collector._fetch()
rows = parse_bods_payload(payload, content_type)
print(f"Mode: {used_mode}")
print(f"HTTP download: {len(payload)/1024/1024:.1f} MB")
print(f"Parsed bus positions: {len(rows):,}")
if rows:
    v = rows[0]
    print(f"Example: {v.name} | {v.operator} | {v.lat:.5f}, {v.lon:.5f} | {v.position_label}")
    print("BODS LIVE GPS TEST: OK")
else:
    raise SystemExit("BODS responded but no current vehicle positions were parsed.")
