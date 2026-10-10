# UK Activity Radar v1.2.1 production audit

Audit period: 5–10 October 2026

## Executive summary

The repository is a viable single-process local application, but its current boundaries will not scale safely to a public or multi-worker deployment. The largest risks are upstream URL trust, unauthenticated quota-bearing proxy routes, false-healthy partial feed failures, unbounded in-memory state, and repeated full-snapshot work in both the WebSocket server and browser.

The stabilization pass deliberately avoided a broad rewrite. It fixed confirmed credential disclosure, URL-validation, WebSocket-origin, launcher, shared-state ownership, and collector-shutdown defects; added deterministic browser coverage; and preserved the existing Useful, Cool, and Live behavior.

No committed private credential or populated `.env` was found. `.env.example` contains blank placeholders and `.env` remains ignored.

## Resolved findings

### High

- **Credential disclosure in public source status.** BODS query-string keys and legacy TomTom query keys could appear in raw `requests` exceptions, which were copied into source status and exposed through diagnostics, readiness, feed-status, and WebSocket payloads. `app/error_safety.py:public_error`, `app/collectors/bods.py:BODSCollector._fetch`, `app/collectors/bods_sx.py:BODSSXCollector._run`, and `app/traffic_flow.py:TrafficFlowService.self_test` now redact sensitive query/header values and explicit secrets.
- **Environment Agency URL-validation bypass.** Prefix-only validation accepted a crafted URL whose apparent EA prefix was actually URL userinfo. `app/smart_places.py:_ea_readings_url` now validates parsed scheme, host, port, credentials, and path. `app/collectors/environment_agency.py:EnvironmentAgencyFloodCollector._safe_area_url` applies equivalent validation to upstream-discovered flood-area links.
- **Cross-site browser WebSocket access.** `app/main.py:websocket_updates` accepted any browser Origin. Browser connections are now same-origin; non-browser clients that omit Origin remain compatible.
- **Destructive launcher behavior.** `run.bat` forcibly terminated any process listening on port 8000. It now reports the owning PID and exits without killing unrelated software.

### Medium

- **Mutable objects escaped the state lock.** `app/state.py:LiveState` stored and returned model instances by reference. Callers could mutate shared state after releasing the lock. State boundaries now use detached copies and have regression coverage.
- **Collector stop did not wait for workers.** Environment Agency and National Highways collectors now make start idempotent and perform a bounded join during stop.
- **Generated data could enter source control.** `.gitignore` now treats `data/` as runtime state while retaining `data/README.md`, and ignores browser/Python test output and dependencies.
- **Browser workflows had no regression coverage.** Deterministic Playwright fixtures now cover map load, Useful/Cool mode, drawer behavior, marker hover/click, station and bus boards, TomTom and weather toggles, maritime detail, Live Audio, detail open/close, badges, and mobile layout.

## Remaining high-priority risks

1. **No shared outbound URL policy.** Several collectors follow URLs discovered in third-party payloads: GBFS discovery feeds and SSEN/ArcGIS metadata in `app/collectors/live_world.py`, warning layers in `app/collectors/met_office.py`, and RNLI-linked data in `app/collectors/rnli.py`. Add one HTTPS-only outbound policy with provider allowlists, private/reserved-address rejection, DNS and redirect revalidation, standard-port enforcement, and response-size limits.
2. **Quota-bearing proxy routes have no deployment protection.** TomTom tile/incident/segment routes in `app/main.py` are unauthenticated and lack rate limiting. Any reachable client can consume the owner's quota. Add deployment authentication, per-client limits, global budgets, and per-cache-key request coalescing before public hosting.
3. **Partial feed failure can look like a healthy empty feed.** `PowerOutageCollector`, GBFS, and UK Air paths in `app/collectors/live_world.py` can swallow provider errors, return an empty snapshot, clear last-known-good data, and report ready. Collectors need structured partial-failure results, stale timestamps, and last-known-good preservation.
4. **In-memory vehicle state grows monotonically.** `app/state.py:LiveState.upsert` has no periodic source-aware expiry/removal. High-churn BODS, AIS, and aircraft IDs can increase memory and scan cost indefinitely. Add source snapshot replacement or periodic expiry with explicit retention rules.
5. **Production multi-worker behavior is invalid.** `app/main.py` constructs global state, collectors, SQLite services, and background jobs at import/lifespan scope. Multiple ASGI workers would duplicate subscriptions and polling, split viewer/state counts, and contend on local databases. Document the single-worker constraint, then separate ingestion from the API/read model before scaling.

## Remaining medium-priority risks

- **Collector lifecycle is inconsistent.** `app/collectors/stomp_client.py`, `app/collectors/aisstream.py`, Smart Places, and bus timetable synchronization have mismatched stop/join behavior and mostly fixed retry intervals. Introduce a collector protocol with bounded shutdown, exponential backoff, jitter, stale-data semantics, and common health fields.
- **WebSocket work is O(clients × objects).** `app/main.py:websocket_updates` repeatedly deep-copies, filters, serializes, and summarizes full state for every client every 1.5 seconds, while duplicating activity information in compatibility fields. Precompute versioned snapshots, send deltas, and serialize off the event loop.
- **Frontend updates churn the whole map.** `app/static/app.js:renderAll` and `renderMap` repeatedly filter/classify/sort activities and update every marker/cluster. Diff by object ID plus update time, memoize classification, and measure before considering MapLibre/WebGL.
- **Frontend is a single coupled module.** `app/static/app.js` combines transport, local storage, map rendering, classification, analytics, dialogs, and audio in one IIFE; `styles.css` contains overlapping generations of layout rules. Extract tested modules and a small state store incrementally.
- **Cool classification is duplicated and frontend-bound.** `app/static/app.js:interestInfo` and `coolInfo` reclassify free text while collectors separately set `interesting`. The planned v1.3 Cool Engine should own structured category, subtype, score, explanation, and badge on the backend, with a compatibility serializer during migration.
- **External frontend assets are not supply-chain hardened.** `app/static/index.html` loads Leaflet/cluster assets from a CDN without SRI and has no CSP/security-header middleware. Pin and self-host assets or add integrity metadata and a tested CSP plus `nosniff`, referrer, and frame policies.
- **Analytics storage grows without retention.** `app/product.py:ProductAnalytics` inserts raw events and computes summaries across all rows. Add retention/rollups and protect analytics endpoints for deployed environments.
- **Large upstream payloads are not bounded.** BODS ZIP/bulk parsing and reference downloads can consume excessive memory/disk if an upstream response is malformed or compromised. Stream with compressed/uncompressed byte limits and ZIP member limits.
- **Configuration is evaluated at import/class definition.** `app/config.py:Settings` parses environment values eagerly; malformed numeric values can crash import and tests cannot reliably construct fresh settings. Move to a validated settings factory with range checks and actionable errors.
- **Runtime side effects occur at import.** `app/main.py` constructs `ProductAnalytics`, schedules, and services globally. Move resource creation to lifespan/application-container boundaries.

## Lower-priority cleanup

- Consolidate repeated distance, datetime, numeric, and XML helpers only after characterization tests exist.
- Remove confirmed dead configuration and functions such as unused DATEX settings and `LiveState.upsert_event`, rather than deleting suspicious code speculatively.
- Replace the launcher's long list of historical ZIP directories with an explicit, versioned data migration.
- Correct version drift in the privacy page and reduce overlapping CSS/version-specific override sections.
- Pin or lock dependency versions for reproducible releases; `run.bat` should not upgrade packages on every normal launch.

## Test coverage and remaining gaps

The Python suite covers parsers, services, state, URL safety, and feed behavior. The Playwright suite uses deterministic recorded frontend fixtures and exercises the major click workflows without credentials or live providers.

The next test increment should add a backend recorded-feed replay harness for Network Rail, BODS, AIS, ADS-B, TomTom, National Highways, RNLI, and weather, plus FastAPI lifespan/route/WebSocket integration tests and collector fault-injection tests. The current Playwright harness protects the browser contract but does not yet prove the complete collector-to-browser pipeline.

## Recommended sequence

1. Shared outbound URL and response-size policy.
2. Authentication/rate limits and request coalescing for quota-bearing routes.
3. Structured partial-failure and last-known-good feed semantics.
4. Source-aware expiry and delta/versioned WebSocket snapshots.
5. Full backend recorded-feed replay and route/lifespan integration coverage.
6. Incremental router/service/frontend module extraction.
7. Begin the separate v1.3 Cool Engine mission.
