# UK Activity Radar v1.2.1 — Useful + Cool

This release reorganises the radar around **why somebody opens it** rather than around data feeds.

- **Useful** — trains, buses, station boards, bus stops, traffic, roadworks/incidents and weather.
- **Cool** — steam/special rail, military or special aircraft, Coastguard/HEMS, RNLI and unusual maritime movements. Routine traffic is deliberately suppressed in this view.
- **Live** — embedded authorised/public audio streams, nearby webcams and live precipitation radar. Aviation/marine/rail-labelled streams are prioritised when a public stream is discoverable; the app does not intercept radio traffic itself.

## v1.2.1 UX changes

- Four-item rail: **Useful / Cool / Live / More**.
- Custom high-contrast discovery badges for STEAM, RAILTOUR, FREIGHT, MILITARY, COASTGUARD, HEMS, NAVAL, LIFEBOAT and other confidently-classified special movements.
- A dedicated **Cool score** and nearby discovery card.
- Cool map markers are visually distinct from routine transport, so unusual activity jumps out immediately.
- The bottom ticker changes between practical LIVE information and COOL discoveries depending on the active experience.
- Live Audio stays inside the site. Specialist aviation/marine/rail-labelled public streams are shown first; normal broadcast radio is collapsed as a secondary fallback.
- Existing v1.2 transport precision, stop/station boards, TomTom traffic, RainViewer radar and private configured feeds are retained.

## Start on Windows

1. Extract the whole folder beside your previous UK Activity Radar folders if possible.
2. Double-click **`run.bat`**.
3. The launcher repairs/migrates existing local settings, reuses expensive rail caches where possible, starts FastAPI and opens `http://127.0.0.1:8000/`.

The normal/shareable release contains **no API keys or account credentials**. A private configured copy can contain the owner's keys in `.env`; do not redistribute that copy.

## v1.2 changes

### Map-first UX redesign

v1.2 deliberately pauses feed expansion and reorganises the product around the map:

- the map remains full-screen; the left interface floats over it and collapses to a narrow rail;
- seven understandable workspaces replace the permanent wall of controls: **Nearby, Transport, Roads, Sea, Weather, Live and More**;
- **Nearby now** continually summarises live transport, roadworks, incidents, weather, rescue aircraft and maritime activity around the current map centre;
- hover cards answer the immediate question with route/status/delay/speed/next point rather than exposing source metadata;
- click opens deeper context on the right, while source IDs, coordinates, train UIDs and similar fields are behind **Technical details**;
- a bottom **LIVE** strip surfaces interesting/current activity and flies the map to an item when clicked;
- moving GPS/AIS/ADS-B objects use a short heading tail instead of large arrowheads; Network Rail reporting-point trains do not get invented headings;
- radio remains playable in-page and lives in its own **Live** workspace;
- feed health, density, map styles, watch zones and experimental controls are moved into **More** so they do not dominate normal use;
- Roadworks and Incidents are separate map filters rather than overlapping views of the same National Highways records.

The intended interaction is now **hover → understand, click → investigate, left rail → change context**.


### Aircraft now identify themselves properly

SAR, HEMS and other special-aircraft cards use the live ADS-B payload for:

- registration when supplied;
- Mode-S / ICAO hex;
- ICAO aircraft type designator;
- a readable manufacturer/model for common UK SAR/HEMS types;
- altitude, groundspeed and track.

Opening the aircraft card then performs a low-rate, 24-hour-cached lookup against **Airport-Data.com** using the exact Mode-S identity. When a matching aircraft thumbnail is available, the card shows that exact aircraft photograph with photographer/source attribution. A thumbnail is rejected if the external registration conflicts with the live ADS-B registration. No random aircraft image is substituted.

### Roads and incidents are separate

National Highways activity is now divided strictly into:

- **🚧 Roadworks** — planned roadworks / planned carriageway or lane closures;
- **⚠ Incidents** — unplanned road incidents / disruption.

One National Highways event is no longer deliberately placed in both categories. TomTom flow/incidents remain visual traffic overlays rather than duplicate activity cards.

### Accessibility clutter removed

The wheelchair/lift-access layer and wheelchair detail items have been removed from the normal tracker UI. TfL cycle-hire remains available where relevant.

### Train positions no longer cut across fields

Network Rail TRUST is not GPS. v1.2 therefore keeps trains at the **last confirmed railway reporting location** instead of drawing a straight-line geographic interpolation between reporting points. This is intentionally less visually smooth but much more honest about where the data actually says the train is.

Direction pointers are also suppressed for rail unless a genuinely useful heading exists. GPS/AIS/ADS-B vehicles use a smaller pointer, only when the source provides a valid bearing/course and the object is moving.

### TomTom traffic updated to Orbis v2

Traffic tiles now try the current TomTom **Orbis Traffic Flow** and **Orbis Traffic Incidents** raster endpoints first, with the older endpoint retained only as a fallback. Traffic credentials remain server-side and are not exposed in browser JavaScript.

### Station boards improved

Station clicks resolve a real passenger station and show a live board. The local Network Rail SCHEDULE index provides richer planned calls once it is ready; while that large first-time index is building, the board can fall back to the live board source so it is not blank.

Local SCHEDULE + TRUST matching uses train UID/headcode/train identity where possible. A matched live train is highlighted on the map rather than choosing an arbitrary nearby train.

### Bus stops now use timetable + live BODS data

AVL alone often cannot answer “what bus is due at this exact stop?”. v1.2 therefore adds a regional BODS GTFS timetable cache. A stop card can combine:

- published BODS stop times;
- operator live due/prediction data when supplied;
- a live BODS vehicle whose GTFS trip ID matches the scheduled trip.

Nearby buses are still never presented as “due here” purely because they are geographically close.

### Radio plays inside the tracker

Nearby public stations in the left-hand Radio section have in-page play controls. The built-in audio player follows the selected station stream; an external-site visit is no longer required for normal listening. Streams that forbid browser playback can still be opened externally.

### Maritime / AIS

AISStream remains the live maritime source. Vessel cards show MMSI, AIS speed/course and exact provider/profile searches. Webcam discovery is location-based around the vessel/harbour, not a search for an onboard webcam.

### SAR rate-limit resilience retained

The ADSB.lol collector rotates across UK regions instead of firing several large point requests in one burst. HTTP 429 responses honour `Retry-After`, keep recent verified positions and show a waiting/rate-limited state rather than incorrectly reporting the source as dead.

## Configuration helpers

- `configure_network_rail.bat` — Network Rail Open Data TRUST/TD/VSTP login
- `configure_traffic.bat` — TomTom key
- `configure_maritime.bat` — AISStream key
- `configure_buses.bat` — BODS key
- `configure_roads.bat` — National Highways key
- `sync_rail_data.bat` — force Network Rail schedule/reference setup

Configuration scripts merge their own fields into `.env`; they no longer replace unrelated credentials. The launcher also repairs the legacy Windows UTF-8 BOM problem that could make the first Network Rail variable unreadable.

## Live sources

| Layer | Source | Setup |
|---|---|---|
| Rail movements | Network Rail TRUST / TD / VSTP | Free Network Rail Open Data account |
| Rail boards | Live board source + local Network Rail SCHEDULE context | Network Rail improves matching |
| Bus live GPS | DfT BODS AVL / GTFS-RT | BODS key |
| Bus published timetable | DfT BODS GTFS timetable | BODS key / public timetable source |
| Planned roadworks / unplanned incidents | National Highways | Developer key |
| Traffic flow / traffic overlay / place search | TomTom Orbis | Developer key |
| Maritime | AISStream | Account/key |
| RNLI context | RNLI public sources | No key |
| SAR / HEMS / special aircraft | ADSB.lol | No key; polite low-rate polling |
| Exact aircraft identity/photo | Airport-Data.com click-time lookup | No key for public lookup path |
| Weather warnings | Met Office | No key |
| Rain radar | RainViewer | No key for current map path |
| Nearby public radio | Radio Browser | No key |

## Accuracy rules

- TRUST positions are railway reporting/signalling locations, not train GPS.
- Bus positions are only shown as stop-specific matches when the feed/timetable identity supports that claim.
- AIS position/course is live vessel telemetry; a calculated approach time is not an operator ferry timetable.
- RNLI station/launch markers are context and do not claim a lifeboat is physically at that point.
- ADS-B/MLAT aircraft positions are labelled as such.
- Exact photos are shown only when there is a defensible identity match.
- Planned roadworks and unplanned incidents are separate activity categories.

## Development checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app
npm ci
npm run check:js
npx playwright install chromium
npm run test:e2e
```

The Playwright suite uses deterministic recorded fixtures and does not require
live feed credentials. Production-audit findings and the stabilization changes
are documented in [`AUDIT_REPORT.md`](AUDIT_REPORT.md) and
[`CHANGELOG_CODEX.md`](CHANGELOG_CODEX.md).
