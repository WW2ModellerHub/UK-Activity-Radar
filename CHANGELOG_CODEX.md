# Codex stabilization changelog

## 2026-11-05 — faster zoom and balanced Discover preview

- Classified Lynx, Wildcat and other named military rotorcraft as helicopters
  and replaced the generic disc with recognisable single-rotor, Apache and
  tandem-rotor Chinook map silhouettes.
- Stopped long live-position interpolation from overriding Leaflet during zoom,
  so markers now remain attached to their map coordinates during the animation.
- Balanced the three-item free Discover preview across aircraft, rail and rescue
  whenever those categories are live, while showing the real national category
  totals and explaining that Radar+ unlocks the full result set.

## 2026-11-04 — public bug reporting

- Added a prominent Report a bug action to the mobile-accessible Network menu,
  the desktop footer and public information pages.
- Added a structured GitHub issue form that requests reproduction steps, device,
  browser and optional screenshots while warning visitors not to publish private
  locations, credentials or payment information.
- Advanced the offline shell cache so existing installed clients receive the
  new support route.

## 2026-11-04 — technical SEO and consent-gated site analytics

- Added canonical, description, social-card and WebSite/WebApplication
  structured metadata to the public radar.
- Added a crawlable About page, XML sitemap and robots policy while keeping
  private API, billing and temporary shared-live URLs out of search results.
- Added environment-driven Search Console verification.
- Added optional GA4 measurement that remains completely unloaded until a
  visitor explicitly opts in, with a persistent decline choice and a control
  to revisit the decision from Network settings.
- Updated the privacy notice and deployment guide for Search Console, sitemap
  submission, GA4 configuration and consent verification.

## 2026-11-04 — distinct aircraft and bus-stop map symbols

- Replaced the shared aircraft outline with recognisable top-down airliner,
  propeller, fast-jet, heavy-transport and helicopter silhouettes, including
  visible engines, propellers, intakes and rotor details at map scale.
- Gave each aircraft class its own colour while retaining the aircraft type
  label beneath the symbol.
- Replaced the moving-bus emoji on fixed bus stops with a dedicated blue
  roadside stop sign and pole, preventing stops being mistaken for vehicles.
- Added browser assertions for bus-stop signage, airliner engines and propeller
  detail, and advanced the mobile asset cache to release 15133.

## 2026-11-03 — safe first Render deployment

- Staged Radar+ behind `BILLING_ENABLED=false` for the first hosted launch so
  missing Stripe credentials cannot abort application startup.
- Pinned the confirmed first-party origin to
  `https://uk-activity-radar.onrender.com`; Stripe remains fail-closed and is
  enabled only after the hosted URL, product, price and webhook are tested.

## 2026-11-03 — mobile location and My Places launch flow

- Removed weekly Missions from the public Discover interface and stopped the
  browser requesting mission data; discoveries remain in the private Field Guide.
- Replaced the generic location control with large `Buses near me` and
  `Aircraft above me` actions designed for first-time phone visitors.
- Added a bounded nearest-NaPTAN-stop lookup so `Buses near me` enables the bus
  layer, centres the map and opens the nearest stop's live departure board.
- Simplified My Radar into saved places, live times, nearby activity and one
  optional travel-alert card; detailed alert choices are now progressive.
- Raised the important mobile controls to 44px or larger and added a 390px
  first-visit browser journey covering location, stop saving and alerts.

## 2026-11-03 — public rail labels, current routes and bounded live state

- Replaced raw TRUST sector and unknown ATOC labels with public operator names
  or the neutral “Rail operator” fallback; numeric sector 29 now resolves to
  West Midlands Trains.
- Removed the unsupported Steam live filter and renamed the reliable 1Z view
  “Special passenger”; freight and measurement filters remain source-backed.
- Prevented a reused train UID from attaching an expired or non-running route:
  live and station-board details now use a schedule valid for today and its
  advertised day of operation.
- Replaced TRUST/SCHEDULE/VSTP jargon in customer-facing rail panels with plain
  descriptions while retaining precise source names in diagnostics.
- Corrected the nearby-aircraft promise to “aircraft received” and reports the
  number of positioned aircraft returned from the feed rather than implying
  universal radar coverage.
- Added five-minute source-aware stale-vehicle cleanup to keep long-running
  bus, aircraft, vessel and rail state bounded without changing map freshness.
- Rechecked the commercial profile: sources without a documented display basis
  remain forced off by `COMMERCIAL_MODE` and the Render Blueprint.

## 2026-11-03 — complete live train routes and warm starts

- Recent confirmed Network Rail positions now warm-start from the persistent
  disk instead of rebuilding the train map one movement at a time after every
  service restart.
- TD-only train describers are joined to today's closest location/time-matched
  SCHEDULE or VSTP run, providing operator, origin, destination and calling
  points when the authoritative timetable can identify the service.

## 2026-11-03 — pre-launch operations and payment disclosure

- Added final Radar+ price, renewal, cancellation, refund-request and Stripe
  processing disclosures to the service terms.
- Expanded privacy disclosure for billing email, customer/subscription IDs,
  secure checkout/access cookies and payment-record retention.
- Added seller name and support email as Render prompts and hard launch-readiness
  gates; legal pages render the configured public values without storing them
  as application secrets.
- Added disk-headroom, enabled-feed health and aggregate request-budget gates to
  readiness, without exposing visitor identifiers or credentials.
- Added a real-deployment Playwright smoke suite and optional twice-hourly
  GitHub Actions monitor driven by the `RADAR_BASE_URL` repository variable.
- Documented uptime, readiness, Render resource and Stripe webhook alerts.

## 2026-11-03 — Radar+ launch activation

- Armed Radar+ in the production Render Blueprint at £3.99/month.
- Kept the five deployment-specific billing values as secret Render prompts;
  no Stripe credential, site URL or token pepper is stored in source.
- Added deployment coverage that prevents billing being silently disabled or
  its required secrets being replaced with committed values.
- Updated the test-to-live Stripe checklist while retaining fail-closed startup
  and test/live webhook-mode validation.

## 2026-11-03 — production performance pass

- Deferred billing, push-alert status and Today-card requests until the
  browser's first idle slice, keeping the cold-start critical path for the map
  and live websocket.
- Stopped source-health-only websocket deltas from forcing a full marker,
  filter and heat-layer render.
- Increased the TomTom proxy's reusable HTTP connection pool and coalesced
  identical cold base-map tile requests after a server restart.
- Added browser diagnostics for deferred startup and skipped unchanged
  snapshots, plus a concurrent tile regression test.

## 2026-11-03 — visual-first live detail and personal radar cleanup

- Moved licensed aircraft/train imagery to the top of live detail, reduced the
  visible fact grid and replaced duplicate identity prose with a compact
  expandable details section.
- Made aircraft imagery a single bounded lookup and added immediate
  type-specific silhouettes, including clearer directional Apache, Chinook and
  helicopter map symbols when a suitable commercial-use photo is unavailable.
- Repaired “What’s above me?” so every returned nearby aircraft remains an
  individual visible marker until the user turns the mode off.
- Extended operator colours across train detail cards, facts and journey
  intelligence instead of limiting them to one header strip.
- Reordered My Radar around places, live relevance, passing services,
  interests and optional alerts; moved following/watch-zone controls into a
  quieter secondary section.
- Simplified Network into map options, a plain live-data summary and optional
  technical diagnostics. Disabled feeds no longer clutter the list, and the
  commercial build continues to hide maritime because AISStream is disabled.
- Reduced initial/return rendering work with smaller Leaflet tile buffering and
  cache-first static assets in the service worker.

## 2026-11-02 — commercial launch hardening

- Changed TomTom basemap caching to honour the provider's current cache
  directive instead of unconditionally advertising a 24-hour lifetime.
- Isolated bounded basemap caching from frequently refreshed traffic tiles and
  exposed non-secret cache hit/miss statistics in provider status.
- Added production Content Security Policy, opener isolation and legacy
  cross-domain policy headers without allowing remote script execution.
- Added explicit launch-readiness checks for the commercial profile, required
  provider configuration and disabled-safe billing/push configuration.
- Added public-beta service terms, linked Privacy, Terms and Data licences in
  the footer, and refreshed the offline application shell.

## 2026-11-02 — operator filters and wider rail liveries

- Added a live Operator filter for train, bus and aircraft layers, with **All
  operators** as the behaviour-preserving default.
- Added a normal Aircraft map switch so airline filters can show individual
  live aircraft without entering Cool mode.
- Expanded colour treatments for 22 passenger, charter and freight railway
  operators, including EMR, Thameslink, TfW, GBRf, DRS and West Coast
  Railways. These are original colour-led treatments and do not copy logos.
- Clear map now also resets the operator filter, preventing a hidden filter
  from making a newly enabled layer appear empty.

## 2026-11-02 — operator identity and licensed imagery

- Added colour-led identity cards for British Airways, Ryanair, easyJet, Jet2,
  TUI, Virgin Atlantic, Loganair, Wizz Air, Emirates and Lufthansa aircraft.
- Added matching treatments for Arriva, Stagecoach, First Bus, National
  Express, Go-Ahead and Transdev vehicles.
- Operator cards are derived from published operator names or standard flight
  callsigns and intentionally do not copy company logos.
- Buses with an exact vehicle reference can now use the existing conservative
  Wikimedia media lookup, with photographer and licence displayed beside the
  image.
- Exact-airframe aircraft photos and type-reference images retain visibly
  different truth labels; weak or non-commercially licensed matches remain
  hidden.

## 2026-11-02 — UI simplification and focus-map pass

- Rewrote the most visible image, discovery, search, journey and alert messages
  in passenger-facing language while preserving source and licence detail.
- Simplified Discover and Network headings, removed duplicate visible Cool
  counters and shortened Radar+ benefit copy.
- Added a one-click **Focus map** mode. It hides both panels without changing
  layer state and restores them with the same button or Escape.
- Kept provider failures and feed status in the collapsed Network view instead
  of exposing implementation terminology in normal map messages.
- Updated the offline shell revision so installed copies receive the new UI.
- Added browser regression coverage for Focus map and the revised truthful
  image and live-incident labels.

## 2026-11-02 — Cool, movement and cold-start usability pass

- Rebuilt Cool controls around four compact categories, a clear on/off switch and a one-tap “What’s above me right now?” aircraft view.
- Bundled the pinned Leaflet, marker-cluster and heat-map distributions locally with their licences, removing three render-blocking CDN dependencies from cold start.
- Reduced the polite four-region ADS-B rotation from roughly two minutes to about 48 seconds while retaining Retry-After rate-limit backoff; ordinary websocket updates now wait at most two seconds.
- Aligned aircraft symbols to their observed movement trail, removed the competing direction pointer and added smooth marker-position transitions.
- Made station-board live rows fetch and focus their exact vehicle immediately; train journeys now expose up to 40 scrollable calling points with substantially shorter, plainer wording.
- Raised journey routes above TomTom traffic, added a high-contrast route casing and a persistent Clear route control.
- Fixed search-result navigation so off-stream activities and geocoded locations focus the map directly.

## 2026-11-02 — Aircraft trails and exact train identity

- Added bounded, browser-local trails for selected aircraft and SAR movements using positions already present in the authorised viewport stream.
- Isolated movement history as renderer-neutral GeoJSON-ready state, establishing the seam needed for a later measured MapLibre/WebGL layer migration without replacing live-feed logic.
- Bundled the MIT-licensed UK Train Dataset and added strict exact-number rolling-stock enrichment. Headcodes and train UIDs are never treated as vehicle numbers.
- Added visible data provenance, licence retention, cache revisioning and automated reference tests.

## 2026-11-01 — v1.5.0 Radar Now and authentic live boards

- Upgraded the live briefing to **Radar Now**, with concise best, new and
  travel-watch cards derived from current activity.
- Added **Passing Next** for saved railway stations and bus stops. Its rows use
  the existing board data and open matched live vehicles.
- Clarified My Radar as **Smart Place Alerts** without changing its explicit
  browser-permission and safe geofencing behaviour.
- Restyled railway and bus boards as recognisable passenger displays without
  copying an operator's branding or altering source data.
- Added an isolated National Highways Digital VMS collector and parser. Only
  actual sign messages receive the motorway-sign treatment.
- Added the official Speed Managed Areas feed. Cleared, suspended, zero-speed,
  expired and future records are excluded; live metric DATEX values are shown
  as their authentic UK mph roundel.
- Added emergency telephones and strategic-road bus stops as an optional
  **Roadside network** layer under Network. The static feed polls hourly and
  never clutters the default map or masquerades as an incident.
- Isolated closures, motorway signs, managed speed and roadside features into
  four independently monitored National Highways sources.
- Shortened passenger-facing setup and provider copy; diagnostics remain under
  Network and Technical details.
- Verified with 201 Python tests, JavaScript syntax checks and 34 Chromium
  workflows.

## 2026-11-01 — boards, incident freshness and sharing

- Made exact live train and bus matches on station/stop boards visibly
  clickable. Selecting one automatically enables the required map layer,
  waits for its viewport snapshot and opens that live vehicle.
- Removed machine-style underscores from BODS origin/destination names and
  recognised `DatedVehicleJourneyRef` where operators use it to link a live
  bus to its published trip.
- Replaced GTFS-oriented failure copy with a plain explanation when an
  operator has not supplied enough data to identify a bus's next stop.
- Limited London Fire Brigade archive cards to reports from the previous 18
  hours so multi-day-old fires cannot remain presented as live incidents.
- Enabled the National Highways planned-closures feed in both the clean default
  and this local configuration; unplanned incidents remain a separate layer.
- Repaired **Share facts** and **Share live page** by keeping native sharing
  inside the original click gesture and adding clipboard/new-tab fallbacks.
- Simplified aircraft-image wording and removed the internal identity-service
  explanation from the public live-detail card.
- Current verification: 195 Python tests and all 33 Chromium browser workflows
  pass.

## 2026-11-01 — live-detail, map and routing refinement

- Reduced the visible secondary-panel refresh interval to 1.5 seconds and the
  national Cool refresh window to eight seconds, while retaining incremental
  marker updates so unchanged live objects are not rebuilt.
- Added a complete **Cool map off** state and an immediate focus marker for a
  national Cool result that has not yet arrived in the current viewport stream.
- Normalised drawer typography and rebuilt Cool ranking rows so badges, names,
  explanations and status no longer overlap at narrow widths.
- Made aircraft type explicit in live detail, including an honest unavailable
  state when the source has not identified the airframe.
- Restricted automatic train imagery to an identified vehicle or class and
  restricted vessel imagery to an exact distinctive name or MMSI match. Generic
  archive pictures are no longer substituted for unknown live objects.
- Enriched Network Rail special movements with source-backed origin,
  destination, operator, service code and calling points. Marketed railtour
  names are shown only when an authoritative source supplies one.
- Turned My Radar interests into radius-entry alerts for trains, buses, planes,
  helicopters and vessels, and made **Choose a place** automatically expose
  nearby stations and stops.
- Added traffic-aware TomTom routing for driving and TomTom routing for walking
  and cycling using the existing server-side key. Public-transport planning
  remains on the independently hosted OpenTripPlanner integration.
- Current verification: 193 Python tests and all 32 Chromium browser workflows
  pass, including a 2,500-object incremental refresh scenario.

## 2026-11-01 — engagement layer

- Added opt-in Web Push commute alerts that can wake the installed site while it
  is closed. A user chooses a saved place, driving/train mode, radius and alert
  hours; the server sends only matching, deduplicated live disruption.
- Added a persistent subscription store, strict browser-push endpoint allowlist,
  VAPID configuration, service-worker notification handling and privacy copy.
- Added **Today on the Radar**, a compact editorial briefing built only from
  current verified state: best live find, new activity and major disruption.
- Upgraded **My Radar 2.0** with saved stations/stops, saved activity interests,
  a live area summary and clear free-versus-Radar+ capacity limits.
- Added stable `/live/{activity}` public pages with identity, freshness,
  confidence and source facts. Share responses never include exact coordinates;
  rescue/SAR activity receives an additional safety warning.
- Stopped unchanged place snapshots from moving markers or refreshing clusters,
  reducing needless map work.
- Hardened the Windows launcher against two simultaneous starts: it now waits
  for a warming v1.4 server, or continues when a transient port-8000 listener
  disappears, instead of reporting a false permanent port conflict.
- Kept traini.ac out of the commercial build after verifying its repository terms;
  retained independently hosted OpenTripPlanner as the commercial routing path.
- Current verification: 190 Python tests and all 31 Chromium browser workflows pass.

## 2026-11-01 — commercial launch foundation + Radar+

- Reframed the product around **Live**, **Discover**, **My Radar** and
  **Network**, keeping the map empty by default and removing duplicate controls.
- Added secure Stripe Checkout, webhook, portal and opaque device-access-token
  infrastructure with server-side route and WebSocket entitlement checks.
- Kept core live data free while gating full Discover, live tracking, faster
  updates, journey planning, cameras, traffic and richer intelligence as Radar+.
- Added a fail-closed commercial profile and public data-licence register.
- Replaced community base-map tiles with server-proxied TomTom display tiles.
- Removed Trainiac, RainViewer radar, embedded radio, Airport-Data imagery and
  the abandoned YouTube/broadcast surface from the commercial application.
- Limited automatic Wikimedia imagery to files whose metadata explicitly permits
  commercial reuse, retaining direct creator, source and licence links.
- Added a free My Radar allowance (three follows and one watch zone) with expanded
  capacity under Radar+.
- Verification at that milestone: 183 Python tests and all 27 Chromium browser workflows
  pass.

## 2026-10-25 — transport intelligence integrations

- Added a defensive client for traini.ac's current `/v1` API to enrich
  click-driven station/train details and rail-only journeys without replacing
  Network Rail TRUST/SCHEDULE. Requests are cached, locally budgeted and honour
  provider backoff; public deployment keeps this non-commercial service off by
  default.
- Added an optional OpenTripPlanner 2 GTFS GraphQL client for real multimodal
  itineraries. An external OTP service and OSM plus GTFS/NeTEx graph are
  required; an unconfigured service returns an explicit unavailable state.
- Upgraded live-bus intelligence by retaining aimed and expected BODS times,
  calculated early/late status and prediction confidence, and by joining a
  clicked live vehicle's GTFS trip ID to its official regional stop sequence.
- Added the actual Street Manager public-open-data path: a signed AWS SNS
  callback, official topic/URL validation, British National Grid conversion,
  replay-resistant version ordering, cancellation tombstones and a durable
  SQLite snapshot. It remains disabled until the public HTTPS callback is
  registered with DfT. The V6 organisation-scoped Reporting adapter is kept
  separate and is never represented as the national public feed.
- Kept the existing National Highways Road & Lane Closures v2.0 collector as
  the deployable road source. Documented that NTIS real-time DATEX requires an
  onboarded SOAP push receiver, so the legacy URL placeholder is not exposed as
  a pretend working integration.
- Kept the OpenRailwayMap infrastructure layer visibly attributed and documented
  the hosted tile service's small-scale, non-commercial constraint.

## 2026-10-24 — local live-feed proxy recovery

- Diagnosed the simultaneous ADS-B, TomTom, BODS, AIS and public-feed failures as one inherited restricted-shell proxy, not missing or invalid API credentials.
- Added a fail-fast launcher preflight for the exact localhost discard proxy so a restricted development process can no longer masquerade as nineteen independent provider failures.
- Kept legitimate configured proxies supported; the application does not clear proxy variables or bypass them with direct retries.
- Added a non-secret health flag so `run.bat` refuses to reopen an already-running restricted instance.
- Sanitized ADSB.lol proxy errors into actionable copy without publishing proxy URLs or credentials.

## 2026-10-24 — v1.4.0 Find & Collect

- Added a trust-first discovery layer to Cool with Now, Missions and Field Guide views.
- Added a server-owned catalogue of 19 genuinely Cool collectibles, stable weekly mission definitions and validated live discovery claims.
- Claims reject stale, simulated, replayed, routine, non-collectible and active rescue/emergency activity; no coordinates are persisted in a discovery record.
- Added a private browser Field Guide with interests, new-since-last-visit context, exact-versus-type identity labels and factual share/copy summaries.
- Kept Field Guide unlocks permanently on the device while resetting mission credit each week; a previously found type must pass live server validation again before it counts in the new week.
- Kept the MVP map-only: no accounts, GPS proof, public profiles, photo uploads, leaderboards or punitive streaks.
- Kept active rescue/RNLI/SAR records in a dedicated, non-collectible context filter instead of allowing them to dominate Best Live Find, rankings or the Cool ticker.
- Added a separate OBS-ready `/broadcast` programme with rotating national,
  Best Spot, air, rail, sea, traffic and weather scenes, plus 16:9 and 9:16
  layouts and an explicit connection-watchdog slate.
- Added a read-only broadcast adapter that emits hashed display keys, sanitized
  text and deliberately coarse 12–50 km positions. Rescue/SAR, synthetic,
  stale and failed-source highlights are suppressed before reaching the browser.
- Raised undersized discovery microcopy and fixed the mobile/tablet map so it fills the viewport beneath a wrapping header.
- Added a production-oriented Render Blueprint with a single-worker 2 GB web
  service, 10 GB persistent cache disk, health check, Python version pin and prompted
  secret environment variables; no credential values are stored in source.
- Added a public-deployment analytics switch. The Render profile accepts the
  existing coarse events as no-ops and creates no analytics database until a
  consent flow and protected operator report exist.
- Documented measured first-boot memory/storage needs, full-live hosting cost,
  provider quota/licensing gates, single-process constraints and launch checks
  in `DEPLOYMENT.md`.
- Added public-mode request budgets, disabled bulk/debug routes, recurring rail
  cache refreshes, age-based BODS timetable refresh and resilient Windows
  SQLite handoff for the national schedule build.
- Verified the promoted national rail index with SQLite quick-check and 721,875
  schedules; the restarted live service reports the schedule cache ready.
- Verification: 139 Python tests and 34 Chromium browser workflows pass.

## 2026-10-16 — v1.3.2 Visuals + Reliability

- Repaired every density mode by including its required activity types in the viewport subscription; All activity requests the complete pool and Off returns to the user's selected Useful layers.
- Added loading, live-point-count and no-data feedback for density overlays, plus deterministic browser coverage for rendering, switching and clearing heat layers.
- Added conservative reference imagery for server-classified steam, railtour/special and measurement trains, without treating a reused headcode as locomotive identity.
- Added explicit exact-vehicle versus representative-image labelling and retained Wikimedia creator, licence and source attribution.
- Enabled the click-driven Wikimedia lookup by default while keeping routine unidentified trains image-free.
- Updated CI to include the root release-wiring checks that normal `pytest.ini` discovery omits.
- Verification: 108 Python tests and 24 Chromium browser workflows pass.

## 2026-10-14 — v1.3.1 Clear Map + Reliable Boards

- Made the default Useful map empty and converted the six practical feeds into independent, explicit on/off cards; WebSocket snapshots are now scoped to the requested types.
- Removed duplicate filters and duplicate density, radar, traffic and Radar+ controls; added one Clear map action for every optional overlay.
- Repaired empty station boards by falling back when the local schedule cache is ready but incomplete.
- Prevented a small VSTP-only database from being mistaken for the complete national timetable cache.
- Limited ferry context to credible inbound AIS targets within 20 km or harbour activity within 5 km, with no claim that AIS is a ferry timetable.
- Added friendly military aircraft type resolution, distinct aircraft/helicopter silhouettes and clearly labelled exact-airframe versus type-reference imagery.
- Expanded browser coverage for the clear start, one-control design and clear-map behaviour.
- Verification: 96 Python tests and 21 Chromium browser workflows pass.

## 2026-10-14 — v1.3 performance and live-feed hardening

- Reworked the live-state hot path so summary counts no longer deep-copy and reclassify the entire national bus snapshot on every browser update.
- Reduced live map delivery to one compact activity array, skipped unchanged snapshots, increased the useful browser cadence from 1.5s to 1.0s, and added zoom-aware limits for routine trains, bikes and dense environmental layers.
- Changed map rendering to update only markers whose position, icon or tooltip actually changed; a 2,500-object unchanged snapshot now causes zero marker/cluster churn.
- Added a small national Cool priority API so Best Spot, rankings and the ticker remain UK-wide while map markers stay viewport-scoped.
- Expanded real radio choices to 12 in the interface / 16 in the backend, coalesced directory requests and retained last-known verified stations through short provider outages.
- Corrected ADSB.lol identity parsing so real aircraft descriptions/operators reach the Cool classifier, and shortened the polite four-region UK refresh from about four minutes to about two.
- Hardened AISStream subscription confirmation, compression, ping/queue handling, retry backoff, malformed frames and shutdown. A replacement key is now validated before it can be saved; the currently stored key is rejected before confirmation and still needs replacing.
- Verification: 89 Python tests and 19 Chromium browser tests pass.

## 2026-10-13 — v1.3.0 Stabilise + Intelligence

- Added the server-owned Cool classifier with explainable category, subtype, score, badge and Best Spot eligibility; Eurofighters and other identified high-value aircraft outrank routine trainers.
- Added Cool rail subtype filters, Best Spot zoom, individual aircraft icons, live-object map tracking and independent practical layer switches.
- Replaced the external webcam shortcut with source-backed on-map TfL JamCam images/video and nearest-camera context for incidents where licensed camera data exists.
- Made RNLI media conservative: only date/place/rescue-matched coverage is presented and generic historic imagery is no longer substituted.
- Made passenger stations immediately available from the cached rail reference, preserving the last good cache through transient Windows locks.
- Bounded and cached aircraft enrichment, removed repeated refresh lookups and retained feed-provided identity while enrichment is unavailable.
- Kept bus density in More, removed the CARTO basemap dependency, and expanded deterministic browser coverage for the new interactions.

## 2026-10-12 — protected local credential persistence

- Added a Windows DPAPI-encrypted, per-user credential backup so new local checkouts restore Network Rail and other configured feeds automatically without exposing secrets to Git.
- Changed TomTom setup to hide key entry and clear its temporary plaintext bridge immediately after use.

## 2026-10-10 — production hosting preparation

- Removed the CARTO dark basemap and its external tile dependency.
- Existing browsers that saved the retired dark-map preference now fall back to the standard map.
- Added browser regression coverage confirming that no CARTO request is made.
- Extended Windows credential migration to recover missing values from previous extracted builds in Downloads, including the extra directory created by ZIP extraction.
- TomTom setup now validates a replacement key against the official Orbis Traffic service before saving it and immediately refreshes the encrypted Windows credential backup.

## 2026-10-10 — v1.2.1 production stabilization

This mission intentionally adds no product features and preserves the Useful, Cool, and Live experiences.

### Repository and automation

- Imported the sanitized v1.2.1 source as the repository baseline; no populated `.env`, credentials, Python caches, or runtime data were included.
- Expanded `.gitignore` so downloaded/generated `data/`, Node dependencies, Playwright output, pytest output, and local work directories cannot be committed accidentally.
- Added reproducible development dependencies in `requirements-dev.txt`.
- Added GitHub Actions checks for Python tests/compilation and deterministic Chromium tests/JavaScript syntax.

### Security

- Added public-error sanitization for credentials embedded in query strings, authorization values, and known secret values.
- Applied sanitization to BODS, BODS SIRI-SX, and TomTom traffic source status.
- Replaced prefix-only Environment Agency URL checks with parsed scheme/host/port/path validation for browser-supplied measure URLs and upstream flood-area links.
- Restricted browser WebSocket connections to the request's own Origin while retaining clients that do not send an Origin header.
- Changed the Windows launcher to report a port-8000 conflict instead of force-terminating the process that owns the port.

### Reliability and concurrency

- Made `LiveState` own and return detached model snapshots so callers cannot mutate shared state outside its lock.
- Made Environment Agency and National Highways collector starts idempotent and shutdown joins bounded.
- Made STOMP retry waits interruptible and worker shutdown bounded.
- Serialized and atomically replaced station-reference merges that can run concurrently at startup.
- Clear BODS stop predictions after a successful empty snapshot so removed/cancelled journeys do not remain stale.
- Preserve outage state on total provider failure by surfacing an error instead of treating the result as a verified empty snapshot.

### Browser regression coverage

- Added a Playwright/Chromium harness with deterministic recorded application, place, station-board, bus-board, radio, weather, traffic, rail, and maritime data.
- Covered map load, Useful/Cool switching, Cool badges, left drawer behavior, marker hover/click, detail open/close, station boards, bus timetables, TomTom toggles, weather radar, maritime selection, Live Audio, and mobile layout.
- Stubbed external tiles/CDN/media interfaces so tests are credential-free and do not depend on live providers.

### Verification

- Python: 58 tests passed.
- Browser: 10 Playwright tests passed in Chromium.
- Python source and tests compile successfully.
- Frontend application and service-worker JavaScript pass Node syntax checks.
- The staged source contains no populated `.env` or detected credential literal.

### Remaining work

See `AUDIT_REPORT.md` for the prioritized remaining risks. The highest priorities are a shared outbound URL policy, protection for quota-bearing proxy routes, structured partial-failure semantics, source-aware state expiry, and backend recorded-feed replay tests. The Cool Engine described here was subsequently delivered in the v1.3.0 work above.
## 2026-11-02 — Exact rolling-stock livery panels

- Turned exact UK Train Dataset number matches into livery-themed train cards.
- Added class, individual number, name, operator/owner, formation, depot, build year, works number, pool and preservation/storage details when present.
- Kept TRUST headcodes and train UIDs out of rolling-stock matching so a service such as `1Z66` cannot be mistaken for a locomotive number.
# 2026-11-03 — Mobile map controls and toast repair

- Replaced the tall phone navigation rail with a compact bottom-sheet layout.
- Collapsed location actions after use on phones so aircraft and bus results are
  immediately visible on the map, with a small one-tap control to reopen it.
- Fixed status toasts stretching between top and bottom edges in mobile Safari.
- Shortened route-detail failure copy and removed the misleading validity claim.
# 2026-11-03 — Hosted mobile cache, routes and aircraft polish

- Versioned the main browser assets and made live CSS/JavaScript network-first
  so deployed phone clients cannot remain stuck on an obsolete layout.
- Made basic train routes and calling points part of the free live detail while
  keeping advanced locating, tracking and the remaining Radar+ paywall intact.
- Put the phone logo, search and Go action on one compact row, kept workspace
  buttons visible, and moved Leaflet zoom controls clear of the menu.
- Replaced the generic helicopter blocks with distinct top-down Chinook,
  Apache and conventional-helicopter silhouettes.
- Kept the four workspace destinations visible in the collapsed phone bar so
  location actions reveal the map without making My Radar unreachable.
