# UK Activity Radar

UK Activity Radar is a live FastAPI map of British transport, roads, weather,
environment and selected special movements. It has four clear experiences:

- **Live** — an empty-by-default map with independent train, bus, roadwork,
  incident, weather and flood switches, station boards and nearby context.
- **Discover** — source-classified unusual aircraft and rail movements, Best
  Live Find, weekly map missions and a private on-device Field Guide.
- **My Radar** — followed activity and saved watch zones.
- **Network** — density, official licensed cameras, source health and advanced
  live-intelligence controls.

Radar Now highlights current verified activity. Saved stations and stops power
Passing Next boards, while Smart Place Alerts can notify a visitor about travel
disruption and selected activity near their places.

The browser receives bounded viewport snapshots over one same-origin WebSocket.
Provider credentials remain server-side. The default map stays clear until the
visitor turns a layer on.

## Commercial public profile

`COMMERCIAL_MODE=true` is fail-closed. Sources without a documented commercial
display/reuse basis are disabled even if an old local `.env` still contains a
key. The public profile uses a direct Mapbox basemap plus server-proxied TomTom traffic tiles and removes
the old radio, weather-radar, broadcast, Trainiac and third-party aircraft-photo
paths. Wikimedia images appear only when their file metadata explicitly permits
commercial reuse and each card links to the creator/source and licence.

The source register and required notices are served at `/data-licences`.
Provider access is not the same as permission to republish: review current terms
before enabling a new feed.

## Radar+

The public map remains usable without payment. Radar+ gates product intelligence,
not exclusive ownership of raw public data:

- full Discover results;
- live object tracking and faster refreshes;
- richer transport and identity detail;
- TomTom traffic, official cameras and multimodal journey planning;
- expanded My Radar capacity.

Stripe Checkout, subscription status, the customer portal, webhook verification,
opaque HttpOnly access cookies and server-side entitlement checks are implemented.
Billing stays disabled until production Stripe values are supplied.

## Run locally

On Windows, double-click `run.bat`. It creates/uses `.venv`, installs the pinned
requirements, preserves the private `.env`, and opens `http://127.0.0.1:8000/`.
If port 8000 is already occupied, close the existing radar process first.

For a shell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Copy `.env.example` to `.env` and add credentials only to `.env`. Never commit,
upload or paste the configured file into an issue or build archive.

Core private settings include:

- `NETWORK_RAIL_USERNAME` / `NETWORK_RAIL_PASSWORD`
- `BODS_API_KEY`
- `NATIONAL_HIGHWAYS_API_KEY`
- `TOMTOM_TRAFFIC_API_KEY`
- `MAPBOX_PUBLIC_TOKEN` (read-only browser token; restrict it to your production domain in Mapbox)

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm run check:js
npm run test:e2e
```

Playwright clicks the real UI, including blank-map startup, layer toggles,
density, Discover, Field Guide, Best Live Find, station and bus boards, TomTom
traffic, cameras, tracking, detail behaviour and mobile layout.

## Deploy

This is not a GitHub Pages site: Python collectors, WebSockets, SQLite caches,
server-side keys and Stripe webhooks require a running web service. GitHub should
hold the clean source; `render.yaml` describes the first commercial Render
deployment. See [DEPLOYMENT.md](DEPLOYMENT.md).

The production design intentionally uses one process and one instance. Scaling
horizontally before externalising state would duplicate polling and split live
state.

## Trust and safety

- Positions are labelled as live GPS, confirmed reports or estimates.
- Reference images are never presented as live photographs.
- Rescue/emergency activity is contextual and never collectible.
- Missions are map-only; visitors are told to stay in lawful public places.
- The site is not an official transport, emergency, navigation or investment
  service. Live feeds can be incomplete, delayed or wrong.

See [CHANGELOG_CODEX.md](CHANGELOG_CODEX.md) for implementation history and
[AUDIT_REPORT.md](AUDIT_REPORT.md) for the original architectural audit.
